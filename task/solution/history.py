"""Effective booking snapshot and booking state history.

Both artifacts derive from the authoritative revision population (contract
section 24-25). History is the revision provenance ledger; the effective
bookings snapshot is the current-state projection. effective_from/effective_to
follow contract section 25: effective_from is the authoritative row's
created_ts; effective_to is the created_ts of the next higher authoritative
revision of the same booking, or empty for the highest.
"""

from collections import defaultdict


def build_effective_rows(effective):
    """One row per booking having an effective revision (sorted by booking_id)."""
    rows = []
    for booking_id in sorted(effective):
        rec = effective[booking_id]
        if rec is None:
            continue
        rows.append({
            "booking_id": booking_id,
            "status": rec["status"],
            "clinic_id": rec["clinic_id"],
            "room_id": rec["room_id"],
            "start_ts": rec["start_ts"],
            "end_ts": rec["end_ts"],
            "provider_id": rec["provider_id"],
            "source": rec["source"],
            "effective_revision": rec["revision"],
        })
    return rows


def build_history_rows(ledger, authoritative, effective):
    """Build the booking state history from authoritative revisions.

    One row per authoritative revision (VOIDED authoritative revisions are
    represented). is_effective is 1 exactly for the booking's effective
    revision when one exists, else 0.
    """
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
            rows.append({
                "booking_id": booking_id,
                "revision": rv,
                "status": rec["status"],
                "clinic_id": rec["clinic_id"],
                "room_id": rec["room_id"],
                "start_ts": rec["start_ts"],
                "end_ts": rec["end_ts"],
                "effective_from": rec["created_ts"],
                "effective_to": effective_to,
                "is_effective": 1 if effective_rev == rv else 0,
            })
    rows.sort(key=lambda r: (r["booking_id"], r["revision"]))
    return rows
