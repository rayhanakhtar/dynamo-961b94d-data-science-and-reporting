#!/usr/bin/env python3
"""Event ingestion component for the room-capacity pipeline.

Reads the operational event feeds, normalizes and validates each row, resolves
identities, and persists canonical events to the state file. Newly supplied
events are appended to the canonical event ledger.

Usage:
  python3 bin/ingest_events.py --state /app/state/state.json \
      --scheduling /path/to/scheduling_events.csv \
      --adjustments /path/to/reservation_adjustments.ndjson \
      --clinics /path/to/clinic_directory.csv \
      --rooms /path/to/room_directory.csv
"""

import argparse
import csv
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import state as state_mod

VALID_STATUSES = {"BOOKED", "CANCELLED", "VOIDED"}


def read_csv_rows(path):
    with open(path, encoding="utf-8", newline="") as h:
        return [dict(r, feed="SCHEDULING") for r in csv.DictReader(h)]


def read_json_lines(path):
    rows = []
    with open(path, encoding="utf-8") as h:
        for line in h:
            line = line.strip()
            if line:
                rows.append(dict(json.loads(line), feed="ADJUSTMENT"))
    return rows


def read_clinic_directory(path):
    mapping = {}
    with open(path, encoding="utf-8", newline="") as h:
        for row in csv.DictReader(h):
            mapping[row["clinic_alias"].strip().upper()] = \
                row["canonical_clinic_id"].strip().upper()
    return mapping


def read_room_directory(path):
    mapping = {}
    with open(path, encoding="utf-8", newline="") as h:
        for row in csv.DictReader(h):
            mapping[(row["clinic_id"].strip().upper(),
                     row["room_alias"].strip().upper())] = \
                row["canonical_room_id"].strip().upper()
    return mapping


def parse_clock(value):
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(value)
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = int(parts[2]) if len(parts) == 3 else 0
    if not (0 <= hours <= 23 and 0 <= minutes <= 59 and 0 <= seconds <= 59):
        raise ValueError(value)
    return hours * 60 + minutes


def normalize_event(raw):
    try:
        event_id = raw["event_id"].strip()
        booking_id = raw["booking_id"].strip()
    except (KeyError, AttributeError):
        return None
    if not event_id or not booking_id:
        return None
    try:
        revision = int(raw["revision"])
        if revision < 0:
            raise ValueError
    except (ValueError, TypeError, KeyError):
        return None
    try:
        status = raw["status"].strip().upper()
        clinic_token = raw["clinic_id"].strip().upper()
        room_token = raw["room_id"].strip().upper()
    except (KeyError, AttributeError):
        return None
    if status not in VALID_STATUSES or not clinic_token or not room_token:
        return None
    try:
        start_date = datetime.date.fromisoformat(raw["start_date"].strip())
        end_date = datetime.date.fromisoformat(raw["end_date"].strip())
        start_minute = parse_clock(raw["start_time"])
        end_minute = parse_clock(raw["end_time"])
        created_ts = raw["created_ts"].strip()
        datetime.datetime.strptime(created_ts, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, IndexError, KeyError, AttributeError):
        return None
    if start_minute is None or end_minute is None:
        return None
    start = (datetime.datetime.combine(start_date, datetime.time(0)) +
             datetime.timedelta(minutes=start_minute))
    end = (datetime.datetime.combine(end_date, datetime.time(0)) +
           datetime.timedelta(minutes=end_minute))
    if end < start:
        return None
    return {
        "event_id": event_id, "booking_id": booking_id, "revision": revision,
        "status": status, "clinic_token": clinic_token,
        "room_token": room_token,
        "provider_id": (raw.get("provider_id") or "").strip().upper(),
        "source": (raw.get("source") or "").strip().upper(),
        "start": start, "end": end, "created_ts": created_ts,
    }


def ingest(scheduling_path, adjustments_path, clinics_path, rooms_path,
           state_path):
    state = state_mod.load_state(state_path)
    raw_sched = read_csv_rows(scheduling_path)
    raw_adj = read_json_lines(adjustments_path)
    clinic_map = read_clinic_directory(clinics_path)
    room_map = read_room_directory(rooms_path)

    combined = raw_sched + raw_adj
    total_raw = len(combined)
    adj_total = len(raw_adj)

    canonical = state["canonical_events"]
    accepted_events = []
    duplicates = 0
    malformed = 0

    # Track event ids seen during THIS ingestion run only. Persisted state is
    # loaded but the persisted ingested_event_ids set is not consulted for
    # deduplication, so re-supplied events are appended again rather than
    # treated as already ingested.
    run_seen = set()
    for row in combined:
        eid = row["event_id"].strip()
        if eid in run_seen:
            duplicates += 1
            continue
        run_seen.add(eid)
        rec = normalize_event(row)
        if rec is None:
            malformed += 1
            continue
        raw_clinic = rec["clinic_token"]
        raw_room = rec["room_token"]
        canonical_clinic = clinic_map.get(raw_clinic, raw_clinic)
        canonical_room = room_map.get((canonical_clinic, raw_room), raw_room)
        clinic_hit = raw_clinic in clinic_map
        room_hit = (canonical_clinic, raw_room) in room_map
        canonical.append({
            "event_id": rec["event_id"],
            "booking_id": rec["booking_id"],
            "revision": rec["revision"],
            "status": rec["status"],
            "clinic_id": canonical_clinic,
            "room_id": canonical_room,
            "provider_id": rec["provider_id"],
            "source": rec["source"],
            "start_ts": rec["start"].strftime("%Y-%m-%d %H:%M:%S"),
            "end_ts": rec["end"].strftime("%Y-%m-%d %H:%M:%S"),
            "created_ts": rec["created_ts"],
            "clinic_hit": clinic_hit,
            "room_hit": room_hit,
        })
        accepted_events.append(rec["event_id"])

    # Keep the persisted event-id set current with what this run accepted so a
    # later run can see the full set even though it is not consulted for
    # dedup above.
    state["ingested_event_ids"] = sorted(
        set(state["ingested_event_ids"]) | set(accepted_events))
    source_key = os.path.abspath(scheduling_path)
    state["sources"][source_key] = {
        "total_raw": total_raw,
        "adj_total": adj_total,
        "duplicates": duplicates,
        "malformed": malformed,
    }
    # The rebuild checkpoint is invalidated for the first ingest of a fresh
    # state. Later incremental ingests leave any prior checkpoint in place,
    # so a resume that trusts the checkpoint may treat an appended batch as
    # already fully processed.
    if not state.get("_first_ingest_done", False):
        state["checkpoint"]["needs_rebuild"] = True
        state["_first_ingest_done"] = True
    state_mod.save_state(state_path, state)
    return {"accepted": len(accepted_events), "duplicates": duplicates,
            "malformed": malformed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default=state_mod.default_state_path())
    parser.add_argument("--scheduling",
                        default=os.environ.get("DYNAMO_INPUT_SCHEDULING",
                                               "/app/data/scheduling_events.csv"))
    parser.add_argument("--adjustments",
                        default=os.environ.get("DYNAMO_INPUT_ADJUSTMENTS",
                                               "/app/data/reservation_adjustments.ndjson"))
    parser.add_argument("--clinics",
                        default=os.environ.get("DYNAMO_INPUT_CLINICS",
                                               "/app/data/clinic_directory.csv"))
    parser.add_argument("--rooms",
                        default=os.environ.get("DYNAMO_INPUT_ROOMS",
                                               "/app/data/room_directory.csv"))
    args = parser.parse_args()
    result = ingest(args.scheduling, args.adjustments, args.clinics, args.rooms,
                    args.state)
    print("accepted=%d duplicates=%d malformed=%d" % (
        result["accepted"], result["duplicates"], result["malformed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
