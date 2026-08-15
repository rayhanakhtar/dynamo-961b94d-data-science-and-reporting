#!/usr/bin/env python3
"""Daily room capacity facts.
Reads the operational event sources for an ambulatory scheduling /
exam-room reservation system and produces analysis-ready room-capacity
artifacts under /app/output. The authoritative specification of all
processing and output semantics is /app/data/docs/fact_contract.md; a
structured schema/rule mirror lives in /app/data/docs/fact_dictionary.toml.
"""
import csv
import datetime
import json
import os
import sys
from collections import defaultdict
INPUT_SCHEDULING = os.environ.get("DYNAMO_INPUT_SCHEDULING",
                                  "/app/data/scheduling_events.csv")
INPUT_ADJUSTMENTS = os.environ.get("DYNAMO_INPUT_ADJUSTMENTS",
                                   "/app/data/reservation_adjustments.ndjson")
INPUT_CLINICS = os.environ.get("DYNAMO_INPUT_CLINICS",
                               "/app/data/clinic_directory.csv")
INPUT_ROOMS = os.environ.get("DYNAMO_INPUT_ROOMS",
                             "/app/data/room_directory.csv")
LEDGER_PATH = os.environ.get("DYNAMO_LEDGER_PATH",
                             "/app/work/canonical_events.csv")
EFFECTIVE_PATH = os.environ.get("DYNAMO_EFFECTIVE_PATH",
                                "/app/output/effective_bookings.csv")
HISTORY_PATH = os.environ.get("DYNAMO_HISTORY_PATH",
                              "/app/output/booking_state_history.csv")
RECONCILIATION_PATH = os.environ.get("DYNAMO_RECONCILIATION_PATH",
                                     "/app/output/capacity_reconciliation.csv")
FACTS_PATH = os.environ.get("DYNAMO_FACTS_PATH",
                            "/app/output/daily_room_capacity_facts.csv")
SUMMARY_PATH = os.environ.get("DYNAMO_SUMMARY_PATH",
                              "/app/output/quality_summary.json")
LEDGER_COLUMNS = ["event_id", "booking_id", "revision", "status", "clinic_id",
                  "room_id", "provider_id", "source", "start_ts", "end_ts",
                  "created_ts"]
EFFECTIVE_COLUMNS = ["booking_id", "status", "clinic_id", "room_id",
                     "start_ts", "end_ts", "provider_id", "source",
                     "effective_revision"]
HISTORY_COLUMNS = ["booking_id", "revision", "status", "clinic_id", "room_id",
                   "start_ts", "end_ts", "effective_from", "effective_to",
                   "is_effective"]
RECONCILIATION_COLUMNS = ["room_id", "clinic_id", "calendar_date",
                          "effective_booking_count", "raw_interval_minutes",
                          "blocked_minutes", "overlap_minutes"]
VALID_STATUSES = {"BOOKED", "CANCELLED", "VOIDED"}

def read_csv_rows(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))

def read_json_lines(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def read_clinic_directory(path):
    mapping = {}
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            mapping[row["clinic_alias"].strip().upper()] = \
                row["canonical_clinic_id"].strip().upper()
    return mapping

def read_room_directory(path):
    mapping = {}
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            mapping[(row["clinic_id"].strip().upper(),
                     row["room_alias"].strip().upper())] = \
                row["canonical_room_id"].strip().upper()
    return mapping

def parse_clock(value):
    """HH:MM or HH:MM:SS -> minutes since midnight (seconds floored)."""
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
    """Return a canonical record dict, or None if the row is malformed."""
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
    return {"event_id": event_id, "booking_id": booking_id,
            "revision": revision, "status": status,
            "clinic_token": clinic_token, "room_token": room_token,
            "provider_id": (raw.get("provider_id") or "").strip().upper(),
            "source": (raw.get("source") or "").strip().upper(),
            "feed": raw.get("feed", ""), "start": start, "end": end,
            "created_ts": created_ts}

def deduplicate(rows):
    """Keep the first copy of each event_id in the input order."""
    seen = set()
    kept = []
    removed = 0
    for row in rows:
        event_id = row["event_id"].strip()
        if event_id in seen:
            removed += 1
            continue
        seen.add(event_id)
        kept.append(row)
    return kept, removed
def resolve_identity(clinic_token, room_token, clinic_map, room_map):
    clinic = clinic_map.get(clinic_token, clinic_token)
    return clinic, room_map.get((clinic, room_token), room_token)

def build_ledger(records, clinic_map, room_map):
    """Resolve identities into canonical records; return (ledger, stats)."""
    ledger = []
    clinic_resolved = 0
    room_resolved = 0
    unresolved = 0
    for rec in records:
        raw_clinic = rec["clinic_token"]
        raw_room = rec["room_token"]
        canonical_clinic, canonical_room = resolve_identity(
            raw_clinic, raw_room, clinic_map, room_map)
        clinic_hit = raw_clinic in clinic_map
        room_hit = (canonical_clinic, raw_room) in room_map
        if clinic_hit:
            clinic_resolved += 1
        if room_hit:
            room_resolved += 1
        if not (clinic_hit and room_hit):
            unresolved += 1
        ledger.append({"event_id": rec["event_id"],
                       "booking_id": rec["booking_id"],
                       "revision": rec["revision"], "status": rec["status"],
                       "clinic_id": canonical_clinic,
                       "room_id": canonical_room,
                       "provider_id": rec["provider_id"],
                       "source": rec["source"],
                       "start_ts": rec["start"].strftime("%Y-%m-%d %H:%M:%S"),
                       "end_ts": rec["end"].strftime("%Y-%m-%d %H:%M:%S"),
                       "created_ts": rec["created_ts"]})
    ledger.sort(key=lambda row: row["event_id"])
    identity_stats = {
        "clinic_aliases_resolved": clinic_resolved,
        "room_aliases_resolved": room_resolved,
        "identity_tokens_unresolved": unresolved,
    }
    return ledger, identity_stats

def resolve_collisions(ledger):
    """Authoritative winner per (booking_id, revision); latest created_ts wins."""
    by_key = {}
    for rec in ledger:
        key = (rec["booking_id"], rec["revision"])
        prior = by_key.get(key)
        if prior is None or (rec["created_ts"], rec["event_id"]) > \
                (prior["created_ts"], prior["event_id"]):
            by_key[key] = rec
    return by_key

def select_effective(ledger, authoritative):
    """Highest non-VOIDED authoritative revision per booking."""
    by_booking = defaultdict(list)
    for rec in ledger:
        by_booking[rec["booking_id"]].append(rec["revision"])
    effective = {}
    for booking_id, revisions in by_booking.items():
        chosen = None
        for revision in sorted(set(revisions), reverse=True):
            candidate = authoritative.get((booking_id, revision))
            if candidate is not None and candidate["status"] != "VOIDED":
                chosen = candidate
                break
        effective[booking_id] = chosen
    return effective

def build_identity_lookup(authoritative, by_booking):
    """Per-booking identity memo from the lowest recorded revision."""
    lookup = {}
    for booking_id, revisions in by_booking.items():
        for revision in sorted(revisions):
            rec = authoritative.get((booking_id, revision))
            if rec is not None:
                lookup[booking_id] = (rec["clinic_id"], rec["room_id"])
                break
    return lookup

def apply_identity_projection(effective, identity_lookup):
    """Effective state with the booking-level identity memo applied."""
    projected = {}
    for booking_id, rec in effective.items():
        if rec is None:
            projected[booking_id] = None
            continue
        clinic_id, room_id = identity_lookup.get(booking_id,
                                                 (rec["clinic_id"],
                                                  rec["room_id"]))
        row = dict(rec)
        row["clinic_id"] = clinic_id
        row["room_id"] = room_id
        projected[booking_id] = row
    return projected

def build_effective_rows(effective):
    """One row per booking with an effective revision (sorted by booking_id)."""
    rows = []
    for booking_id in sorted(effective):
        rec = effective[booking_id]
        if rec is None:
            continue
        rows.append({"booking_id": booking_id, "status": rec["status"],
                     "clinic_id": rec["clinic_id"],
                     "room_id": rec["room_id"],
                     "start_ts": rec["start_ts"], "end_ts": rec["end_ts"],
                     "provider_id": rec["provider_id"],
                     "source": rec["source"],
                     "effective_revision": rec["revision"]})
    return rows

def build_history_rows(ledger, authoritative, effective):
    """Booking state history from authoritative revisions."""
    by_booking = defaultdict(list)
    for rec in ledger:
        by_booking[rec["booking_id"]].append(rec["revision"])
    rows = []
    for booking_id in sorted(by_booking):
        revisions = sorted(set(by_booking[booking_id]))
        auth_by_rev = {}
        for rv in revisions:
            rec = authoritative.get((booking_id, rv))
            if rec is not None:
                auth_by_rev[rv] = rec
        effective_rec = effective.get(booking_id)
        effective_rev = None if effective_rec is None else effective_rec["revision"]
        for rv in revisions:
            rec = auth_by_rev.get(rv)
            if rec is None:
                continue
            higher = [x for x in revisions if x > rv]
            next_rev = min(higher) if higher else None
            effective_to = (auth_by_rev[next_rev]["created_ts"]
                            if next_rev is not None else "")
            rows.append({"booking_id": booking_id, "revision": rv,
                         "status": rec["status"],
                         "clinic_id": rec["clinic_id"],
                         "room_id": rec["room_id"],
                         "start_ts": rec["start_ts"],
                         "end_ts": rec["end_ts"],
                         "effective_from": rec["created_ts"],
                         "effective_to": effective_to,
                         "is_effective": 1 if effective_rev == rv else 0})
    rows.sort(key=lambda r: (r["booking_id"], r["revision"]))
    return rows

def day_segments(start_ts, end_ts):
    """Split an interval into per-calendar-day pieces (half-open)."""
    start = datetime.datetime.strptime(start_ts, "%Y-%m-%d %H:%M:%S")
    end = datetime.datetime.strptime(end_ts, "%Y-%m-%d %H:%M:%S")
    day_start = datetime.datetime.combine(start.date(), datetime.time(0))
    while day_start < end:
        day_end = day_start + datetime.timedelta(days=1)
        lo = start if start > day_start else day_start
        hi = end if end < day_end else day_end
        if hi > lo:
            yield day_start.date(), lo, hi
        day_start = day_end

def interval_minutes(start, end):
    return int((end - start).total_seconds()) // 60

def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged

def union_minutes(intervals):
    return sum(interval_minutes(lo, hi) for lo, hi in merge_intervals(intervals))

def build_facts(effective, ledger):
    """Blocked minutes per (room, clinic, day): the shared-room union."""
    day_timelines = defaultdict(list)
    def add_rec(rec):
        if rec is None or rec["status"] != "BOOKED":
            return
        for day, lo, hi in day_segments(rec["start_ts"], rec["end_ts"]):
            day_timelines[(rec["room_id"], rec["clinic_id"], day)].append(
                (lo, hi))
    for rec in effective.values():
        add_rec(rec)
    for rec in ledger:
        add_rec(rec)
    facts = []
    for (room_id, clinic_id, day), intervals in day_timelines.items():
        minutes = union_minutes(intervals)
        if minutes > 0:
            facts.append((room_id, clinic_id, day, minutes))
    facts.sort(key=lambda row: (row[0], row[1], row[2].isoformat()))
    return facts

def count_multi_day(effective):
    count = 0
    for rec in effective.values():
        if rec is None or rec["status"] != "BOOKED":
            continue
        days = set()
        for day, _lo, _hi in day_segments(rec["start_ts"], rec["end_ts"]):
            days.add(day)
        if len(days) > 1:
            count += 1
    return count

def build_reconciliation(effective, authoritative):
    """Per room/clinic/day conservation ledger, sorted by room/clinic/date."""
    by_day = defaultdict(lambda: defaultdict(list))
    def add_rec(rec):
        if rec is None or rec["status"] != "BOOKED":
            return
        for day, lo, hi in day_segments(rec["start_ts"], rec["end_ts"]):
            by_day[(rec["room_id"], rec["clinic_id"], day)][
                rec["booking_id"]].append((lo, hi))
    for rec in effective.values():
        add_rec(rec)
    for rec in authoritative.values():
        add_rec(rec)
    rows = []
    for (room_id, clinic_id, day), contrib in by_day.items():
        raw = sum(union_minutes(iv) for iv in contrib.values())
        all_intervals = [seg for iv in contrib.values() for seg in iv]
        blocked = union_minutes(all_intervals)
        rows.append({"room_id": room_id, "clinic_id": clinic_id,
                     "calendar_date": day.isoformat(),
                     "effective_booking_count": len(contrib),
                     "raw_interval_minutes": raw,
                     "blocked_minutes": blocked,
                     "overlap_minutes": raw - blocked})
    rows.sort(key=lambda r: (r["room_id"], r["clinic_id"], r["calendar_date"]))
    return rows

def build_categorical_summary(total_raw, duplicates, malformed, records,
                              effective, fact_rows, reconciliation,
                              identity_stats, adjustment_total):
    """Categorized quality summary dict with deterministic order."""
    eff_list = list(effective.values())
    adj_valid = sum(1 for rec in records if rec["source"] == "ADJUSTMENT")
    event_quality = {
        "total_raw_events": total_raw,
        "duplicate_events_removed": duplicates,
        "malformed_rows_rejected": malformed,
        "adjustment_rows_valid": adj_valid,
        "adjustment_rows_rejected": adjustment_total - adj_valid,
    }
    identity_quality = dict(identity_stats)
    revision_quality = {
        "total_booking_ids": len(effective),
        "voided_revisions": sum(1 for rec in records
                                if rec["status"] == "VOIDED"),
        "bookings_with_effective_revision":
            sum(1 for rec in eff_list if rec is not None),
        "active_bookings": sum(
            1 for rec in eff_list if rec is not None
            and rec["status"] == "BOOKED"),
        "cancelled_bookings": sum(
            1 for rec in eff_list if rec is not None
            and rec["status"] == "CANCELLED"),
        "bookings_with_no_effective_revision":
            sum(1 for rec in eff_list if rec is None),
        "multi_day_active_bookings": count_multi_day(effective),
    }
    capacity_quality = {
        "total_blocked_minutes": sum(m for _r, _c, _d, m in fact_rows),
        "room_clinic_days_with_overlap": sum(
            1 for r in reconciliation if r["raw_interval_minutes"] >
            r["blocked_minutes"]),
        "overlap_minutes_total": sum(r["overlap_minutes"]
                                     for r in reconciliation),
    }
    reconciliation_mismatches = sum(
        1 for r in reconciliation
        if r["raw_interval_minutes"] != r["blocked_minutes"] +
        r["overlap_minutes"] or r["overlap_minutes"] < 0)
    reconciliation_quality = {
        "reconciled_room_days": len(reconciliation),
        "reconciliation_mismatches": reconciliation_mismatches,
    }
    return {
        "event_quality": event_quality,
        "identity_quality": identity_quality,
        "revision_quality": revision_quality,
        "capacity_quality": capacity_quality,
        "reconciliation_quality": reconciliation_quality,
    }

def write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row[col] for col in columns])

def write_facts(path, fact_rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["room_id", "clinic_id", "calendar_date",
                         "blocked_minutes"])
        for room_id, clinic_id, day, minutes in fact_rows:
            writer.writerow([room_id, clinic_id, day.isoformat(), minutes])

def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2)
        handle.write("\n")

def load_inputs():
    return (read_csv_rows(INPUT_SCHEDULING),
            read_json_lines(INPUT_ADJUSTMENTS),
            read_clinic_directory(INPUT_CLINICS),
            read_room_directory(INPUT_ROOMS))

def process(scheduling, adjustments, clinics, rooms):
    """Transform loaded source data into the required output structures."""
    # Stage 1: each source is de-duplicated on its own before the two streams
    # are joined into one processing stream.
    sched_rows, sched_duplicates = deduplicate(scheduling)
    adj_rows, adj_duplicates = deduplicate(adjustments)
    combined = sched_rows + adj_rows
    total_raw = len(scheduling) + len(adjustments)
    duplicates = sched_duplicates + adj_duplicates
    # Stage 2: validate every surviving row.
    records = []
    malformed = 0
    for row in combined:
        rec = normalize_event(row)
        if rec is None:
            malformed += 1
        else:
            records.append(rec)
    # Stage 3-4-5: identity resolution and canonical ledger.
    ledger, identity_stats = build_ledger(records, clinics, rooms)
    # Stage 6: cross-source collision resolution.
    authoritative = resolve_collisions(ledger)
    # Stage 7: effective revision.
    effective = select_effective(ledger, authoritative)
    by_booking = defaultdict(list)
    for rec in ledger:
        by_booking[rec["booking_id"]].append(rec["revision"])
    identity_lookup = build_identity_lookup(authoritative, by_booking)
    effective = apply_identity_projection(effective, identity_lookup)
    # Stage 8: effective bookings + history.
    effective_rows = build_effective_rows(effective)
    history_rows = build_history_rows(ledger, authoritative, effective)
    # Materialization + reconciliation + quality.
    fact_rows = build_facts(effective, ledger)
    reconciliation_rows = build_reconciliation(effective, authoritative)
    summary = build_categorical_summary(
        total_raw, duplicates, malformed, records, effective, fact_rows,
        reconciliation_rows, identity_stats, len(adjustments))
    return {
        "ledger": ledger,
        "effective_rows": effective_rows,
        "history_rows": history_rows,
        "fact_rows": fact_rows,
        "reconciliation_rows": reconciliation_rows,
        "summary": summary,
    }

def write_outputs(result):
    write_csv(LEDGER_PATH, LEDGER_COLUMNS, result["ledger"])
    write_csv(EFFECTIVE_PATH, EFFECTIVE_COLUMNS, result["effective_rows"])
    write_csv(HISTORY_PATH, HISTORY_COLUMNS, result["history_rows"])
    write_csv(RECONCILIATION_PATH, RECONCILIATION_COLUMNS,
              result["reconciliation_rows"])
    write_facts(FACTS_PATH, result["fact_rows"])
    write_json(SUMMARY_PATH, result["summary"])

def main():
    missing = [p for p in (INPUT_SCHEDULING, INPUT_ADJUSTMENTS, INPUT_CLINICS,
                           INPUT_ROOMS) if not os.path.exists(p)]
    if missing:
        sys.stderr.write("input(s) not found: %s\n" % ", ".join(missing))
        return 2
    data = load_inputs()
    result = process(*data)
    write_outputs(result)
    return 0
if __name__ == "__main__":
    sys.exit(main())