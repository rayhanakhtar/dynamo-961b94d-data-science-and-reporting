#!/usr/bin/env python3
"""
Daily room capacity facts.

Reads the ambulatory scheduling / exam-room reservation export and reports,
for every clinic room and calendar day, how many minutes the room is blocked
by scheduled appointments, along with a short quality summary of the export.

The export can contain duplicate event rows, malformed rows, cancelled and
voided records, and bookings that were amended over time. Those cases are
handled before the capacity calculation so that only the current, valid
reservations are counted.

Input : /app/data/scheduling_events.csv
Output: /app/output/daily_room_capacity_facts.csv
        /app/output/quality_summary.json
"""

import csv
import datetime
import json
import os
import sys
from collections import defaultdict

INPUT_PATH = "/app/data/scheduling_events.csv"
FACTS_PATH = "/app/output/daily_room_capacity_facts.csv"
SUMMARY_PATH = "/app/output/quality_summary.json"

VALID_STATUSES = {"BOOKED", "CANCELLED", "VOIDED"}

FACT_COLUMNS = ["room_id", "clinic_id", "calendar_date", "blocked_minutes"]

SUMMARY_KEYS = [
    "total_raw_events",
    "duplicate_events_removed",
    "malformed_rows_rejected",
    "total_booking_ids",
    "voided_revisions",
    "bookings_with_effective_revision",
    "active_bookings",
    "cancelled_bookings",
    "bookings_with_no_effective_revision",
    "multi_day_active_bookings",
    "room_clinic_days_with_overlap",
    "total_blocked_minutes",
]


def parse_clock(value):
    """Parse an HH:MM or HH:MM:SS wall-clock time into minutes since midnight.

    Seconds, when present, are dropped: a booking occupies the whole minute
    in which it starts.
    """
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(value)
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = int(parts[2]) if len(parts) == 3 else 0
    if not (0 <= hours <= 23 and 0 <= minutes <= 59 and 0 <= seconds <= 59):
        raise ValueError(value)
    return hours * 60 + minutes


def read_export(path):
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def drop_duplicate_events(rows):
    """Keep the first occurrence of each event id, counting later copies."""
    seen = set()
    kept = []
    dropped = 0
    for row in rows:
        event_id = row["event_id"].strip()
        if event_id in seen:
            dropped += 1
            continue
        seen.add(event_id)
        kept.append(row)
    return kept, dropped


def normalize_row(row):
    """Trim and type-check one export row; return None if the row is bad."""
    event_id = row["event_id"].strip()
    booking_id = row["booking_id"].strip()
    if not event_id or not booking_id:
        return None

    try:
        revision = int(row["revision"])
        if revision < 0:
            raise ValueError
    except ValueError:
        return None

    status = row["status"].strip().upper()
    clinic_id = row["clinic_id"].strip().upper()
    room_id = row["room_id"].strip().upper()
    if status not in VALID_STATUSES or not clinic_id or not room_id:
        return None

    try:
        start_date = datetime.date.fromisoformat(row["start_date"].strip())
        end_date = datetime.date.fromisoformat(row["end_date"].strip())
        start_minute = parse_clock(row["start_time"])
        end_minute = parse_clock(row["end_time"])
        created_ts = row["created_ts"].strip()
        datetime.datetime.strptime(created_ts, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, IndexError):
        return None

    start = (datetime.datetime.combine(start_date, datetime.time(0)) +
             datetime.timedelta(minutes=start_minute))
    end = (datetime.datetime.combine(end_date, datetime.time(0)) +
           datetime.timedelta(minutes=end_minute))
    if end < start:
        return None

    return {
        "event_id": event_id,
        "booking_id": booking_id,
        "revision": revision,
        "status": status,
        "clinic_id": clinic_id,
        "room_id": room_id,
        "provider_id": row["provider_id"].strip().upper(),
        "source": row["source"].strip().upper(),
        "start": start,
        "end": end,
        "created_ts": created_ts,
    }


def normalize_rows(rows):
    """Validate every surviving row; return clean records plus the count of
    rows that were set aside as malformed."""
    records = []
    rejected = 0
    for row in rows:
        record = normalize_row(row)
        if record is None:
            rejected += 1
        else:
            records.append(record)
    return records, rejected


def resolve_current_state(records):
    """Pick the revision that describes each booking's current state.

    A booking can appear many times as it was amended, cancelled, or booked
    again over time; the row with the highest revision number is the most
    recent statement about that booking. If the same revision appears more
    than once, the most recently created row wins.
    """
    by_booking = defaultdict(dict)
    for record in records:
        prior = by_booking[record["booking_id"]].get(record["revision"])
        if prior is None or (record["created_ts"], record["event_id"]) > \
                (prior["created_ts"], prior["event_id"]):
            by_booking[record["booking_id"]][record["revision"]] = record

    current = {}
    for booking_id, revisions in by_booking.items():
        current[booking_id] = revisions[max(revisions)]
    return current


def day_segments(record):
    """Split a booking's half-open interval into per-calendar-day pieces.

    A minute belongs to the day in which it starts, so a booking that crosses
    midnight contributes part of its time to each day.
    """
    day_start = datetime.datetime.combine(record["start"].date(),
                                          datetime.time(0))
    while day_start < record["end"]:
        day_end = day_start + datetime.timedelta(days=1)
        lo = record["start"] if record["start"] > day_start else day_start
        hi = record["end"] if record["end"] < day_end else day_end
        if hi > lo:
            yield day_start.date(), lo, hi
        day_start = day_end


def interval_minutes(start, end):
    return int((end - start).total_seconds()) // 60


def merge_intervals(intervals):
    """Merge overlapping half-open intervals into one timeline."""
    merged = []
    for start, end in sorted(intervals):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def union_minutes(intervals):
    """Number of minutes covered by the union of the given intervals."""
    return sum(interval_minutes(lo, hi) for lo, hi in merge_intervals(intervals))


def build_facts(current):
    """Compute blocked minutes for every (room, clinic, day).

    Appointments are scheduled per provider: the reservations of a single
    provider on a room are merged into one timeline, so a provider's own
    double-booked slots count only once, and the room's occupied time for the
    day is the sum of the time reserved by each provider. Days with no
    occupied time are not emitted.
    """
    provider_timelines = defaultdict(list)
    for record in current.values():
        if record["status"] != "BOOKED":
            continue
        for day, lo, hi in day_segments(record):
            key = (record["room_id"], record["clinic_id"],
                   record["provider_id"], day)
            provider_timelines[key].append((lo, hi))

    facts = defaultdict(int)
    for (room_id, clinic_id, _provider_id, day), intervals in \
            provider_timelines.items():
        minutes = sum(interval_minutes(lo, hi)
                      for lo, hi in merge_intervals(intervals))
        facts[(room_id, clinic_id, day)] += minutes

    return sorted(
        (room_id, clinic_id, day, minutes)
        for (room_id, clinic_id, day), minutes in facts.items()
        if minutes > 0
    )


def count_overlap_days(current):
    """Count room-days on which reservations from different providers collide.

    A collision is a shared minute between at least two reservations that
    belong to different providers on the same room and day.
    """
    day_timelines = defaultdict(list)
    for record in current.values():
        if record["status"] != "BOOKED":
            continue
        for day, lo, hi in day_segments(record):
            day_timelines[(record["room_id"], record["clinic_id"],
                           day)].append((lo, hi))

    overlap_days = 0
    for intervals in day_timelines.values():
        if union_minutes(intervals) < \
                sum(interval_minutes(lo, hi) for lo, hi in intervals):
            overlap_days += 1
    return overlap_days


def build_summary(rows, dropped, rejected, records, current, fact_rows,
                  overlap_days):
    voided_rows = sum(1 for rec in records if rec["status"] == "VOIDED")
    active = sum(1 for rec in current.values() if rec["status"] == "BOOKED")
    cancelled = sum(1 for rec in current.values()
                    if rec["status"] == "CANCELLED")
    dropped_bookings = sum(1 for rec in current.values()
                           if rec["status"] == "VOIDED")
    multi_day = 0
    for rec in current.values():
        if rec["status"] != "BOOKED":
            continue
        days = {day for day, _lo, _hi in day_segments(rec)}
        if len(days) > 1:
            multi_day += 1

    return {
        "total_raw_events": len(rows),
        "duplicate_events_removed": dropped,
        "malformed_rows_rejected": rejected,
        "total_booking_ids": len(current),
        "voided_revisions": voided_rows,
        "bookings_with_effective_revision": len(current) - dropped_bookings,
        "active_bookings": active,
        "cancelled_bookings": cancelled,
        "bookings_with_no_effective_revision": dropped_bookings,
        "multi_day_active_bookings": multi_day,
        "room_clinic_days_with_overlap": overlap_days,
        "total_blocked_minutes": sum(minutes
                                     for _r, _c, _d, minutes in fact_rows),
    }


def write_facts(path, fact_rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(FACT_COLUMNS)
        for room_id, clinic_id, day, minutes in fact_rows:
            writer.writerow([room_id, clinic_id, day.isoformat(), minutes])


def write_summary(path, summary):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({key: summary[key] for key in SUMMARY_KEYS}, handle,
                  indent=2)
        handle.write("\n")


def main():
    if not os.path.exists(INPUT_PATH):
        sys.stderr.write("input not found: %s\n" % INPUT_PATH)
        return 2

    rows = read_export(INPUT_PATH)
    surviving, dropped = drop_duplicate_events(rows)
    records, rejected = normalize_rows(surviving)
    current = resolve_current_state(records)
    fact_rows = build_facts(current)
    overlap_days = count_overlap_days(current)
    summary = build_summary(rows, dropped, rejected, records, current,
                            fact_rows, overlap_days)

    write_facts(FACTS_PATH, fact_rows)
    write_summary(SUMMARY_PATH, summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
