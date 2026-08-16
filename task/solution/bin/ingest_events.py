#!/usr/bin/env python3
"""Reference ingestion component for the stateful room-capacity pipeline.

Reads the operational event feeds, normalizes and validates each row, dedups
against the persisted canonical state (first occurrence of an event_id in
ingestion order wins), resolves identities, and appends the newly accepted
canonical events to the persisted state.

Usage:
  python3 bin/ingest_events.py --state /app/state/state.json \
      --scheduling /path/to/scheduling_events.csv \
      --adjustments /path/to/reservation_adjustments.ndjson \
      --clinics /path/to/clinic_directory.csv \
      --rooms /path/to/room_directory.csv

Environment overrides honored when flags are omitted:
  DYNAMO_INPUT_SCHEDULING / DYNAMO_INPUT_ADJUSTMENTS /
  DYNAMO_INPUT_CLINICS / DYNAMO_INPUT_ROOMS / DYNAMO_STATE_PATH
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parse  # noqa: E402
import normalize  # noqa: E402
import resolve  # noqa: E402
from bin import state as state_mod  # noqa: E402


def ingest(scheduling_path, adjustments_path, clinics_path, rooms_path,
           state_path):
    state = state_mod.load_state(state_path)
    raw_sched = parse.read_scheduling_events(scheduling_path)
    raw_adj = parse.read_reservation_adjustments(adjustments_path)
    clinic_map = parse.read_clinic_directory(clinics_path)
    room_map = parse.read_room_directory(rooms_path)

    combined = raw_sched + raw_adj
    total_raw = len(combined)
    adj_total = len(raw_adj)

    ingested_ids = set(state["ingested_event_ids"])
    canonical = state["canonical_events"]
    accepted_events = []
    duplicates = 0
    malformed = 0
    # Dedup first: the first occurrence of each event_id in the combined
    # stream is the dedup survivor regardless of validity; every later copy
    # is a duplicate. Then validate survivors.
    survivors = []
    for row in combined:
        eid = row["event_id"].strip()
        if eid in ingested_ids:
            duplicates += 1
            continue
        ingested_ids.add(eid)
        survivors.append(row)
    for row in survivors:
        rec = normalize.normalize_event(row)
        if rec is None:
            malformed += 1
            continue
        raw_clinic = rec["clinic_token"]
        raw_room = rec["room_token"]
        canonical_clinic = resolve.resolve_clinic(raw_clinic, clinic_map)
        canonical_room = resolve.resolve_room(canonical_clinic, raw_room,
                                              room_map)
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

    state["ingested_event_ids"] = sorted(ingested_ids)
    source_key = os.path.abspath(scheduling_path)
    # Idempotency: if this exact source was already ingested, leave the state
    # unchanged (no duplicate canonical events, no stat recount).
    if source_key in state["sources"]:
        state_mod.save_state(state_path, state)
        return {"accepted": 0, "duplicates": 0, "malformed": 0}
    state["sources"][source_key] = {
        "total_raw": total_raw,
        "adj_total": adj_total,
        "duplicates": duplicates,
        "malformed": malformed,
    }
    # New canonical events invalidate any previously rebuilt derived state.
    state["checkpoint"]["needs_rebuild"] = True
    if accepted_events:
        state["authoritative"] = {}
        state["effective"] = {}
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
