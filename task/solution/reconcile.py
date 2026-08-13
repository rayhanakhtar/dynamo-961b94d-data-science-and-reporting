"""Capacity reconciliation (contract section 26).

Independently computes the conservation ledger per room/clinic/day from
effective state: raw_interval_minutes, blocked_minutes (shared union),
overlap_minutes. The invariant raw = blocked + overlap must hold on every row.
"""

from collections import defaultdict

from materialize import day_segments, union_minutes


def build_reconciliation(effective):
    """Per room/clinic/day conservation ledger, sorted by room/clinic/date."""
    by_day = defaultdict(lambda: defaultdict(list))
    for rec in effective.values():
        if rec is None or rec["status"] != "BOOKED":
            continue
        for day, lo, hi in day_segments(rec["start_ts"], rec["end_ts"]):
            by_day[(rec["room_id"], rec["clinic_id"], day)][
                rec["booking_id"]].append((lo, hi))

    rows = []
    for (room_id, clinic_id, day), contrib in by_day.items():
        raw = sum(union_minutes(iv) for iv in contrib.values())
        all_intervals = [seg for iv in contrib.values() for seg in iv]
        blocked = union_minutes(all_intervals)
        rows.append({
            "room_id": room_id,
            "clinic_id": clinic_id,
            "calendar_date": day.isoformat(),
            "effective_booking_count": len(contrib),
            "raw_interval_minutes": raw,
            "blocked_minutes": blocked,
            "overlap_minutes": raw - blocked,
        })
    rows.sort(key=lambda r: (r["room_id"], r["clinic_id"], r["calendar_date"]))
    return rows
