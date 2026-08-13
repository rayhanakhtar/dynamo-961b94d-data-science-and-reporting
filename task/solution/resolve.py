"""Deterministic identity resolution, collision resolution, and effective
revision selection (contract sections 20-22, 5, 7).

Clinic resolution precedes room resolution because room aliases are
clinic-scoped. Collision resolution uses all validated survivors; the latest
created_ts wins, the greatest event_id (compared as strings) breaks ties, and
VOIDED rows participate. Only effective-revision selection skips VOIDED.
"""

from collections import defaultdict


def resolve_clinic(token, clinic_map):
    """Canonical clinic: directory mapping or normalized-token fallback."""
    return clinic_map.get(token, token)


def resolve_room(clinic, token, room_map):
    """Canonical room: clinic-scoped directory mapping or token fallback."""
    return room_map.get((clinic, token), token)


def build_ledger(records, clinic_map, room_map):
    """Resolve identities and produce canonical event records.

    Returns ``(ledger, identity_stats)`` where ``identity_stats`` counts over
    the validated event population how many clinic/room tokens resolved via
    the directories and how many fell back to the normalized token.
    """
    ledger = []
    clinic_resolved = 0
    room_resolved = 0
    unresolved = 0
    for rec in records:
        raw_clinic = rec["clinic_token"]
        raw_room = rec["room_token"]
        canonical_clinic = resolve_clinic(raw_clinic, clinic_map)
        canonical_room = resolve_room(canonical_clinic, raw_room, room_map)
        clinic_hit = raw_clinic in clinic_map
        room_hit = (canonical_clinic, raw_room) in room_map
        if clinic_hit:
            clinic_resolved += 1
        if room_hit:
            room_resolved += 1
        if not (clinic_hit and room_hit):
            unresolved += 1
        ledger.append({
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
        })
    ledger.sort(key=lambda row: row["event_id"])
    identity_stats = {
        "clinic_aliases_resolved": clinic_resolved,
        "room_aliases_resolved": room_resolved,
        "identity_tokens_unresolved": unresolved,
    }
    return ledger, identity_stats


def resolve_collisions(ledger):
    """Pick the authoritative row for each (booking_id, revision).

    Winner: latest created_ts, ties broken by greatest event_id (compared as
    strings). VOIDED rows participate in collision resolution.
    """
    by_key = {}
    for rec in ledger:
        key = (rec["booking_id"], rec["revision"])
        prior = by_key.get(key)
        if prior is None or (rec["created_ts"], rec["event_id"]) > \
                (prior["created_ts"], prior["event_id"]):
            by_key[key] = rec
    return by_key


def select_effective(ledger, authoritative):
    """Select the highest non-VOIDED authoritative revision per booking."""
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
