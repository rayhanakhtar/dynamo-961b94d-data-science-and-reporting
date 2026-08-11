#!/usr/bin/env python3
"""
Reference oracle for the "Daily Room Capacity Facts" task.

Reads the ambulatory scheduling / exam-room reservation event export and the
authoritative business rules in /app/data/docs/fact_contract.md, and produces:

  /app/output/daily_room_capacity_facts.csv
  /app/output/quality_summary.json

This is a genuine computation of the task. It depends only on the Python
standard library and the public input file; it contains no fixture-specific
expected values and never accesses test or expected-output directories.

Pipeline stages (mirroring the contract's sections):
  parsing/normalization  -> event deduplication -> validation
  -> revision resolution -> active-booking selection
  -> day materialization -> overlap/union        -> aggregation
  -> quality summary     -> output serialization
"""

import csv
import datetime
import json
import os
import sys


# ---------------------------------------------------------------------------
# I/O paths
# ---------------------------------------------------------------------------

DATA_INPUT = os.environ.get("DYNAMO_INPUT_DATA", "/app/data/scheduling_events.csv")
OUTPUT_CSV = os.environ.get("DYNAMO_OUTPUT_FACTS",
                            "/app/output/daily_room_capacity_facts.csv")
OUTPUT_JSON = os.environ.get("DYNAMO_OUTPUT_JSON",
                             "/app/output/quality_summary.json")


# ---------------------------------------------------------------------------
# Stage 1: parsing and normalization (contract sections 3, 9, 16)
# ---------------------------------------------------------------------------

def normalize_tag(value):
    """Categorical tag: trimmed, case-insensitive, canonical upper-case."""
    return value.strip().upper()


def normalize_id(value):
    """Opaque identifier: trimmed only, case-sensitive."""
    return value.strip()


class Event:
    """One raw event row, normalized per contract section 3."""

    __slots__ = ("event_id", "booking_id", "revision", "status", "clinic_id",
                 "room_id", "provider_id", "source", "start_date",
                 "start_time", "end_date", "end_time", "created_ts",
                 "start", "end")

    def __init__(self, raw):
        self.event_id = normalize_id(raw[0])
        self.booking_id = normalize_id(raw[1])
        self.revision = parse_revision(raw[2])
        self.status = normalize_tag(raw[3])
        self.clinic_id = normalize_tag(raw[4])
        self.room_id = normalize_tag(raw[5])
        self.provider_id = normalize_tag(raw[6])
        self.source = normalize_tag(raw[7])
        self.start_date = parse_calendar_date(raw[8])
        self.start_time = parse_clock_time(raw[9])
        self.end_date = parse_calendar_date(raw[10])
        self.end_time = parse_clock_time(raw[11])
        self.created_ts = parse_timestamp(raw[12])
        # half-open interval [start, end) as minute-aligned datetimes
        self.start = None
        self.end = None


def parse_revision(text):
    """Non-negative integer revision; None if malformed."""
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def parse_clock_time(text):
    """HH:MM or HH:MM:SS -> minutes-of-day (seconds floored); None if bad."""
    parts = text.split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
        seconds = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59 and 0 <= seconds <= 59):
        return None
    return hours * 60 + minutes


def parse_calendar_date(text):
    """YYYY-MM-DD -> datetime.date; None if malformed."""
    try:
        return datetime.date.fromisoformat(text)
    except (ValueError, TypeError):
        return None


def parse_timestamp(text):
    """YYYY-MM-DD HH:MM:SS -> datetime.datetime; None if malformed."""
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def interval_endpoints(event):
    """Compute the half-open interval endpoints from date + time fields."""
    start = (datetime.datetime.combine(event.start_date, datetime.time(0)) +
             datetime.timedelta(minutes=event.start_time))
    end = (datetime.datetime.combine(event.end_date, datetime.time(0)) +
           datetime.timedelta(minutes=event.end_time))
    return start, end


def validate(event):
    """Return a non-empty problem list iff the row is malformed (section 16).

    Malformed-row detection runs after deduplication and never invalidates
    other well-formed rows of the same booking."""
    problems = []
    if not event.event_id:
        problems.append("empty_event_id")
    if not event.booking_id:
        problems.append("empty_booking_id")
    if event.revision is None:
        problems.append("bad_revision")
    if event.status not in ("BOOKED", "CANCELLED", "VOIDED"):
        problems.append("bad_status")
    if not event.clinic_id:
        problems.append("empty_clinic_id")
    if not event.room_id:
        problems.append("empty_room_id")
    if event.start_date is None:
        problems.append("bad_start_date")
    if event.end_date is None:
        problems.append("bad_end_date")
    if event.start_time is None:
        problems.append("bad_start_time")
    if event.end_time is None:
        problems.append("bad_end_time")
    if event.created_ts is None:
        problems.append("bad_created_ts")
    if not problems:
        start, end = interval_endpoints(event)
        if end < start:
            problems.append("end_before_start")
        else:
            event.start, event.end = start, end
    return problems


# ---------------------------------------------------------------------------
# Stage 2-3: event deduplication and validation (section 4)
# ---------------------------------------------------------------------------

def load_events(path):
    """Read the export. Deduplicate by event_id (first copy in file order
    wins; every later copy counts toward duplicate_events_removed), then
    validate the surviving copies. Returns:
      (clean_events, duplicate_count, malformed_count, total_raw_count)."""
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            return [], 0, 0, 0
        raw_rows = list(reader)

    surviving = []
    duplicate_count = 0
    seen_events = set()
    for raw in raw_rows:
        event_id = normalize_id(raw[0])
        if event_id in seen_events:
            duplicate_count += 1
            continue
        seen_events.add(event_id)
        surviving.append(raw)

    clean = []
    malformed_count = 0
    for raw in surviving:
        event = Event(raw)
        if validate(event):
            malformed_count += 1
        else:
            clean.append(event)

    return clean, duplicate_count, malformed_count, len(raw_rows)


# ---------------------------------------------------------------------------
# Stage 4-5: revision resolution and active-booking selection (sections 5-8)
# ---------------------------------------------------------------------------

def resolve_bookings(clean_events):
    """Return {booking_id: effective Event or None}.

    Steps: group by booking_id; for each (booking_id, revision) keep the row
    with the latest created_ts (tie-break: greatest event_id as a string);
    then scan revisions descending, skipping voided, to find the effective
    revision. A booking with no non-voided revision maps to None."""
    by_booking = {}
    for event in clean_events:
        by_booking.setdefault(event.booking_id, {}) \
            .setdefault(event.revision, []).append(event)

    authoritative = {}
    for booking_id, revisions in by_booking.items():
        for revision, events in revisions.items():
            authoritative[(booking_id, revision)] = max(
                events, key=lambda e: (e.created_ts, e.event_id))

    result = {}
    for booking_id, revisions in by_booking.items():
        chosen = None
        for revision in sorted(revisions, reverse=True):
            candidate = authoritative[(booking_id, revision)]
            if candidate.status != "VOIDED":
                chosen = candidate
                break
        result[booking_id] = chosen
    return result


# ---------------------------------------------------------------------------
# Stage 6-7: day materialization and overlap/union (sections 9-12)
# ---------------------------------------------------------------------------

def minutes_by_day(start, end):
    """Half-open [start, end) -> {date: minutes}. A minute belongs to the
    calendar day in which it starts (section 10)."""
    result = {}
    day_start = datetime.datetime.combine(start.date(), datetime.time(0))
    while day_start < end:
        day_end = day_start + datetime.timedelta(days=1)
        seg_start = start if start > day_start else day_start
        seg_end = end if end < day_end else day_end
        if seg_end > seg_start:
            result[day_start.date()] = \
                int((seg_end - seg_start).total_seconds()) // 60
        day_start = day_end
    return result


def segments_by_day(start, end):
    """Same materialization as minutes_by_day but returns the raw
    (seg_start, seg_end, day) tuples needed for union computation."""
    result = []
    day_start = datetime.datetime.combine(start.date(), datetime.time(0))
    while day_start < end:
        day_end = day_start + datetime.timedelta(days=1)
        seg_start = start if start > day_start else day_start
        seg_end = end if end < day_end else day_end
        if seg_end > seg_start:
            result.append((seg_start, seg_end, day_start.date()))
        day_start = day_end
    return result


def union_minutes(segments):
    """Union length in minutes of half-open [start, end) segments.
    Touching segments (one ends exactly when the next begins) are not
    overlapping but their minutes both count (section 12)."""
    total = 0
    current_start = None
    current_end = None
    for seg_start, seg_end in sorted(segments):
        if current_start is None:
            current_start, current_end = seg_start, seg_end
        elif seg_start < current_end:
            if seg_end > current_end:
                current_end = seg_end
        else:
            total += int((current_end - current_start).total_seconds()) // 60
            current_start, current_end = seg_start, seg_end
    if current_start is not None:
        total += int((current_end - current_start).total_seconds()) // 60
    return total


def segment_minutes(seg_start, seg_end):
    return int((seg_end - seg_start).total_seconds()) // 60


# ---------------------------------------------------------------------------
# Stage 8: aggregation into daily facts (sections 11, 13, 14, 15)
# ---------------------------------------------------------------------------

def compute_facts(effective):
    """Return (facts, overlap_days, multi_day_active).

    Capacity grain is (clinic_id, room_id). All active bookings on that grain
    compete for the same room regardless of provider or source; blocked time
    is the union of occupied minutes, never the sum. Only grains with at
    least one blocked minute emit a row, sorted per contract section 15."""
    by_grain_day = {}
    for event in effective.values():
        if event is None or event.status != "BOOKED":
            continue
        for seg_start, seg_end, day in segments_by_day(event.start, event.end):
            key = (event.clinic_id, event.room_id, day)
            by_grain_day.setdefault(key, []).append((seg_start, seg_end))

    facts = []
    overlap_days = 0
    for (clinic_id, room_id, day), segments in by_grain_day.items():
        union = union_minutes(segments)
        summed = sum(segment_minutes(s, e) for s, e in segments)
        if union < summed:
            overlap_days += 1
        facts.append((room_id, clinic_id, day, union))

    facts.sort(key=lambda row: (row[0], row[1], row[2].isoformat()))
    return facts, overlap_days


def count_multi_day_active(effective):
    count = 0
    for event in effective.values():
        if event is not None and event.status == "BOOKED" and \
                len(minutes_by_day(event.start, event.end)) > 1:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Stage 9-10: quality summary and output serialization (sections 15, 17, 18)
# ---------------------------------------------------------------------------

def build_summary(clean_events, effective, facts, overlap_days, multi_day_active,
                  total_raw, duplicates, malformed):
    summary = {
        "total_raw_events": total_raw,
        "duplicate_events_removed": duplicates,
        "malformed_rows_rejected": malformed,
        "total_booking_ids": len(effective),
        "voided_revisions": sum(1 for e in clean_events if e.status == "VOIDED"),
        "bookings_with_effective_revision":
            sum(1 for e in effective.values() if e is not None),
        "active_bookings":
            sum(1 for e in effective.values()
                if e is not None and e.status == "BOOKED"),
        "cancelled_bookings":
            sum(1 for e in effective.values()
                if e is not None and e.status == "CANCELLED"),
        "bookings_with_no_effective_revision":
            sum(1 for e in effective.values() if e is None),
        "multi_day_active_bookings": multi_day_active,
        "room_clinic_days_with_overlap": overlap_days,
        "total_blocked_minutes": sum(row[3] for row in facts),
    }

    # Consistency identities from contract section 17 (hold for any input).
    assert summary["total_booking_ids"] == \
        summary["bookings_with_effective_revision"] + \
        summary["bookings_with_no_effective_revision"]
    assert summary["active_bookings"] + summary["cancelled_bookings"] == \
        summary["bookings_with_effective_revision"]
    return summary


def write_csv(path, facts):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["room_id", "clinic_id", "calendar_date",
                         "blocked_minutes"])
        for room_id, clinic_id, day, minutes in facts:
            writer.writerow([room_id, clinic_id, day.isoformat(), minutes])


def write_json(path, summary):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not os.path.exists(DATA_INPUT):
        sys.stderr.write("Input not found: %s\n" % DATA_INPUT)
        return 2

    clean, duplicates, malformed, total_raw = load_events(DATA_INPUT)
    effective = resolve_bookings(clean)
    facts, overlap_days = compute_facts(effective)
    multi_day_active = count_multi_day_active(effective)

    summary = build_summary(clean, effective, facts, overlap_days,
                            multi_day_active, total_raw, duplicates, malformed)

    write_csv(OUTPUT_CSV, facts)
    write_json(OUTPUT_JSON, summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
