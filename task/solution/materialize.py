"""Day materialization and shared room/clinic union (contract sections 9-15).

Effective BOOKED bookings only. Intervals are half-open [start, end); seconds
are already truncated. A minute belongs to the day in which it starts.
Provider/source never partition capacity.
"""

import datetime
from collections import defaultdict


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


def build_facts(effective):
    """Blocked minutes per (room, clinic, day): the shared-room union."""
    day_timelines = defaultdict(list)
    for rec in effective.values():
        if rec is None or rec["status"] != "BOOKED":
            continue
        for day, lo, hi in day_segments(rec["start_ts"], rec["end_ts"]):
            day_timelines[(rec["room_id"], rec["clinic_id"], day)].append(
                (lo, hi))

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
