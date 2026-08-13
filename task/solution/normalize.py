"""Independent contract normalization for the event feeds.

Normalizes a raw event row from either feed into a canonical record dict, or
None if the row is malformed (contract section 16). Also provides the
cross-source event deduplication rule (contract section 4): the first copy of
each event_id in the combined feed order is kept.
"""

import datetime

VALID_STATUSES = {"BOOKED", "CANCELLED", "VOIDED"}


def parse_clock(value):
    """Parse HH:MM or HH:MM:SS -> minutes since midnight (seconds floored)."""
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
    """Normalize one raw event row; return a canonical record dict or None.

    A row is malformed if any required field is empty or unparsable, if the
    interval is inverted, or if required keys are missing. Seconds are
    truncated to the minute.
    """
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

    provider_id = (raw.get("provider_id") or "").strip().upper()
    source = (raw.get("source") or "").strip().upper()
    feed = raw.get("feed", "")

    return {
        "event_id": event_id,
        "booking_id": booking_id,
        "revision": revision,
        "status": status,
        "clinic_token": clinic_token,
        "room_token": room_token,
        "provider_id": provider_id,
        "source": source,
        "feed": feed,
        "start": start,
        "end": end,
        "created_ts": created_ts,
    }


def deduplicate(rows):
    """Keep the first copy of each event_id in file order.

    Returns (kept, removed_count).
    """
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
