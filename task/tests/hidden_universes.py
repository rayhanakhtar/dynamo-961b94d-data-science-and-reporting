"""Deterministic hidden input universes for semantic-generalization grading.

Each generator returns (scheduling_rows, adjustment_rows) plus the shared
clinic/room directories. The universes obey only the semantics documented in
fact_contract.md. They are generated inside the verifier at grading time and
are never written to the agent-visible /app/data directory.

All generators are deterministic: fixed seeds, no dependence on wall-clock
time or filesystem state.
"""

import random

UNIVERSE_NAMES = [
    "fresh_identities",
    "source_order_dedup",
    "collision_topology",
    "identity_reassignment",
    "temporal_capacity",
    "population_boundaries",
    "composed",
    "large_scale",
]

# Shared fresh identity pools (distinct from the public fixture).
FRESH_CLINICS = [
    ("CLIN-E", ["CLIN-E", "EPC", "AMB-E"]),
    ("CLIN-F", ["CLIN-F", "FPC", "AMB-F"]),
    ("CLIN-G", ["CLIN-G", "GPC", "AMB-G"]),
    ("CLIN-H", ["CLIN-H", "HPC", "AMB-H"]),
]

# Room aliases are clinic-scoped. EXAM-NEW maps to XRM-1 under CLIN-E and to
# XRM-2 under CLIN-F (the documented divergence rule).
FRESH_ROOMS = {
    "CLIN-E": [("XRM-1", ["XRM-1", "EX-1"]), ("XRM-2", ["XRM-2", "EX-2"]),
               ("XRM-3", ["XRM-3", "EX-3"]), ("XRM-1", ["EXAM-NEW"])],
    "CLIN-F": [("XRM-1", ["XRM-1", "EX-1"]), ("XRM-2", ["XRM-2", "EX-2"]),
               ("XRM-4", ["XRM-4", "EX-4"]), ("XRM-2", ["EXAM-NEW"])],
    "CLIN-G": [("XRM-5", ["XRM-5", "EX-5"]), ("XRM-6", ["XRM-6", "EX-6"])],
    "CLIN-H": [("XRM-7", ["XRM-7", "EX-7"]), ("XRM-8", ["XRM-8", "EX-8"])],
}

CLINIC_ALIAS = {}
for _canon, aliases in FRESH_CLINICS:
    for alias in aliases:
        CLINIC_ALIAS[alias.upper()] = _canon.upper()
ROOM_ALIAS = {}
for _clinic, entries in FRESH_ROOMS.items():
    for canon, aliases in entries:
        for alias in aliases:
            ROOM_ALIAS[(_clinic.upper(), alias.upper())] = canon.upper()


def _clinic_directory():
    rows = []
    for canon, aliases in FRESH_CLINICS:
        for alias in aliases:
            rows.append({"canonical_clinic_id": canon, "clinic_alias": alias})
    return rows


def _room_directory():
    rows = []
    for clinic, entries in FRESH_ROOMS.items():
        for canon, aliases in entries:
            for alias in aliases:
                rows.append({"clinic_id": clinic, "room_alias": alias,
                             "canonical_room_id": canon})
    return rows


def _resolve_clinic(token):
    return CLINIC_ALIAS.get(token.upper(), token.upper())


def _resolve_room(clinic, token):
    return ROOM_ALIAS.get((clinic.upper(), token.upper()), token.upper())


def _sched(eid, bid, rev, status, clinic, room, sd, st, ed, et, created,
           provider="P-HID", source="PORTAL"):
    return {"event_id": eid, "booking_id": bid, "revision": str(rev),
            "status": status, "clinic_id": clinic, "room_id": room,
            "provider_id": provider, "source": source,
            "start_date": sd, "start_time": st, "end_date": ed,
            "end_time": et, "created_ts": created}


def _adj(eid, bid, rev, status, clinic, room, sd, st, ed, et, created,
         provider="P-HID", source="ADJUSTMENT"):
    return {"event_id": eid, "booking_id": bid, "revision": rev,
            "status": status, "clinic_id": clinic, "room_id": room,
            "provider_id": provider, "source": source,
            "start_date": sd, "start_time": st, "end_date": ed,
            "end_time": et, "created_ts": created}


# ---------------------------------------------------------------------------
# Family 1: FRESH IDENTITIES
# ---------------------------------------------------------------------------

def _gen_fresh_identities(rng):
    sched = []
    eid = 1001
    bid = 1001
    for i in range(30):
        clinic_alias = rng.choice(["EPC", "FPC", "GPC", "HPC", "AMB-E", "AMB-F"])
        room_alias = rng.choice(["EX-1", "EX-2", "EX-3", "EX-5", "EX-7"])
        day = "2026-04-%02d" % rng.randint(1, 15)
        st = "%02d:%02d" % (rng.randint(8, 15), rng.choice([0, 15, 30, 45]))
        eh = min(21, int(st[:2]) + rng.randint(1, 3))
        sched.append(_sched("HID-%04d" % eid, "HBK-%04d" % bid, 1,
                            rng.choice(["booked", "booked", "cancelled"]),
                            clinic_alias, room_alias, day, st, day,
                            "%02d:%02d" % (eh, int(st[3:])),
                            "2026-03-%02d 08:%02d:00" % (rng.randint(1, 28),
                                                          rng.randint(0, 59))))
        eid += 1
        bid += 1
    # A few 2-revision bookings.
    for i in range(6):
        bid0 = bid
        sched.append(_sched("HID-%04d" % eid, "HBK-%04d" % bid, 1, "booked",
                            "EPC", "EX-1", "2026-04-16", "09:00",
                            "2026-04-16", "10:00", "2026-04-10 08:00:00"))
        eid += 1
        sched.append(_sched("HID-%04d" % eid, "HBK-%04d" % bid, 2,
                            rng.choice(["booked", "cancelled", "voided"]),
                            "EPC", "EX-1", "2026-04-16", "09:00",
                            "2026-04-16", "10:00", "2026-04-10 09:00:00"))
        eid += 1
        bid += 1
    return sched, []


# ---------------------------------------------------------------------------
# Family 2: SOURCE ORDER / DEDUP
# ---------------------------------------------------------------------------

def _gen_source_order_dedup(rng):
    sched = []
    adj = []
    eid = 2001
    bid = 2001
    for i in range(20):
        clinic = "CLIN-E"
        room = "EX-1"
        day = "2026-05-%02d" % rng.randint(1, 15)
        st = "%02d:%02d" % (rng.randint(8, 14), 0)
        eh = int(st[:2]) + 1
        e = "HSD-%04d" % eid
        sched.append(_sched(e, "HSBK-%04d" % bid, 1, "booked", clinic, room,
                            day, st, day, "%02d:00" % eh,
                            "%s 06:%02d:00" % (day, rng.randint(0, 59))))
        # Cross-source duplicate event id in the adjustment feed: the
        # scheduling copy is first in combined order and must win.
        if i % 3 == 0:
            adj.append(_adj(e, "HSBK-%04d" % bid, 1, "voided", clinic, room,
                            day, st, day, "%02d:00" % eh,
                            "%s 08:%02d:00" % (day, rng.randint(0, 59))))
        # Within-feed duplicate in scheduling (identical content).
        if i % 5 == 0:
            sched.append(_sched(e, "HSBK-%04d" % bid, 1, "booked", clinic,
                                room, day, st, day, "%02d:00" % eh,
                                "%s 07:00:00" % day))
        eid += 1
        bid += 1
    return sched, adj


# ---------------------------------------------------------------------------
# Family 3: COLLISION / REVISION TOPOLOGY
# ---------------------------------------------------------------------------

def _gen_collision_topology(rng):
    sched = []
    adj = []
    eid = 3001
    bid = 3001
    statuses = ["booked", "cancelled", "voided"]
    for i in range(40):
        clinic = rng.choice(["CLIN-E", "CLIN-F"])
        room = "EX-1"
        day = "2026-06-%02d" % rng.randint(1, 15)
        st = "%02d:%02d" % (rng.randint(8, 13), 0)
        eh = int(st[:2]) + 1
        nrev = rng.choice([1, 1, 2, 2, 3, 4])
        # Non-contiguous revision numbers, including values above 9 (the
        # contract requires only a non-negative whole number, no upper bound).
        pool = list(range(1, 16))
        revs = sorted(rng.sample(pool, nrev))
        for rv in revs:
            status = rng.choice(statuses)
            ts = "%s %02d:%02d:00" % (day, 6 + rv, rng.randint(0, 30))
            sched.append(_sched("HCT-%04d" % eid, "HCB-%04d" % bid, rv, status,
                                clinic, room, day, st, day, "%02d:00" % eh,
                                ts))
            eid += 1
        # Occasional adjustment row with a created_ts tie and event_id tiebreak.
        if nrev >= 2 and i % 4 == 0:
            top = max(revs)
            adj.append(_adj("HCT-%04d" % eid, "HCB-%04d" % bid, top,
                            rng.choice(statuses), clinic, room, day, st, day,
                            "%02d:00" % eh,
                            "%s %02d:%02d:00" % (day, 6 + top, 0)))
            eid += 1
        bid += 1
    return sched, adj


# ---------------------------------------------------------------------------
# Family 4: IDENTITY REASSIGNMENT
# ---------------------------------------------------------------------------

def _gen_identity_reassignment(rng):
    sched = []
    eid = 4001
    bid = 4001
    for i in range(30):
        day = "2026-07-%02d" % rng.randint(1, 15)
        st = "%02d:%02d" % (rng.randint(8, 13), 0)
        eh = int(st[:2]) + 1
        nrev = rng.choice([2, 2, 3])
        clinics = rng.sample(["CLIN-E", "CLIN-F", "CLIN-G"], nrev)
        for rv in range(1, nrev + 1):
            clinic = clinics[rv - 1]
            # EXAM-NEW diverges by clinic; a concrete room token stays as-is.
            room = rng.choice(["EXAM-NEW", "EX-1", "XRM-5"])
            status = "booked" if rv == nrev else rng.choice(
                ["booked", "cancelled", "voided"])
            sched.append(_sched("HIR-%04d" % eid, "HIB-%04d" % bid, rv, status,
                                clinic, room, day, st, day, "%02d:00" % eh,
                                "%s %02d:%02d:00" % (day, 6 + rv,
                                                     rng.randint(0, 20))))
            eid += 1
        bid += 1
    return sched, []


# ---------------------------------------------------------------------------
# Family 5: TEMPORAL / CAPACITY
# ---------------------------------------------------------------------------

def _gen_temporal_capacity(rng):
    sched = []
    eid = 5001
    clinic = "CLIN-E"
    day = "2026-08-05"
    b = 5001
    # touching intervals
    sched.append(_sched("HTM-%04d" % eid, "HTB-%04d" % b, 1, "booked",
                        clinic, "EX-1", day, "09:00", day, "10:00",
                        day + " 06:00:00"))
    eid += 1
    b += 1
    sched.append(_sched("HTM-%04d" % eid, "HTB-%04d" % b, 1, "booked",
                        clinic, "EX-1", day, "10:00", day, "11:00",
                        day + " 06:05:00"))
    eid += 1
    b += 1
    # overlapping intervals
    sched.append(_sched("HTM-%04d" % eid, "HTB-%04d" % b, 1, "booked",
                        clinic, "EX-2", day, "09:00", day, "12:00",
                        day + " 06:10:00"))
    eid += 1
    b += 1
    sched.append(_sched("HTM-%04d" % eid, "HTB-%04d" % b, 1, "booked",
                        clinic, "EX-2", day, "10:00", day, "13:00",
                        day + " 06:15:00"))
    eid += 1
    b += 1
    # nested interval
    sched.append(_sched("HTM-%04d" % eid, "HTB-%04d" % b, 1, "booked",
                        clinic, "EX-3", day, "09:00", day, "15:00",
                        day + " 06:20:00"))
    eid += 1
    b += 1
    sched.append(_sched("HTM-%04d" % eid, "HTB-%04d" % b, 1, "booked",
                        clinic, "EX-3", day, "10:30", day, "11:30",
                        day + " 06:25:00"))
    eid += 1
    b += 1
    # zero-length BOOKED
    sched.append(_sched("HTM-%04d" % eid, "HTB-%04d" % b, 1, "booked",
                        clinic, "EX-3", day, "16:00", day, "16:00",
                        day + " 06:30:00"))
    eid += 1
    b += 1
    # multi-day interval (day1 14:00 -> day3 10:00)
    sched.append(_sched("HTM-%04d" % eid, "HTB-%04d" % b, 1, "booked",
                        clinic, "EX-4", "2026-08-05", "14:00", "2026-08-07",
                        "10:00", "2026-08-05 06:35:00"))
    eid += 1
    b += 1
    # midnight boundary (23:30 -> next day 00:30)
    sched.append(_sched("HTM-%04d" % eid, "HTB-%04d" % b, 1, "booked",
                        clinic, "EX-4", "2026-08-05", "23:30", "2026-08-06",
                        "00:30", "2026-08-05 06:40:00"))
    eid += 1
    b += 1
    # a cancelled-effective booking that must not contribute
    sched.append(_sched("HTM-%04d" % eid, "HTB-%04d" % b, 1, "booked",
                        clinic, "EX-5", "2026-08-06", "09:00", "2026-08-06",
                        "10:00", "2026-08-05 06:45:00"))
    eid += 1
    sched.append(_sched("HTM-%04d" % eid, "HTB-%04d" % b, 2, "cancelled",
                        clinic, "EX-5", "2026-08-06", "09:00", "2026-08-06",
                        "10:00", "2026-08-05 07:00:00"))
    eid += 1
    b += 1
    return sched, []


# ---------------------------------------------------------------------------
# Family 6: POPULATION BOUNDARIES
# ---------------------------------------------------------------------------

def _gen_population_boundaries(rng):
    sched = []
    adj = []
    eid = 6001
    clinic = "CLIN-F"
    day = "2026-09-05"
    b = 6001
    # VOIDED-only booking
    sched.append(_sched("HPB-%04d" % eid, "HPBK-%04d" % b, 1, "voided",
                        clinic, "EX-1", day, "09:00", day, "10:00",
                        day + " 06:00:00"))
    eid += 1
    sched.append(_sched("HPB-%04d" % eid, "HPBK-%04d" % b, 2, "voided",
                        clinic, "EX-1", day, "09:00", day, "10:00",
                        day + " 07:00:00"))
    eid += 1
    b += 1
    # CANCELLED-effective booking
    sched.append(_sched("HPB-%04d" % eid, "HPBK-%04d" % b, 1, "booked",
                        clinic, "EX-2", day, "09:00", day, "10:00",
                        day + " 06:00:00"))
    eid += 1
    sched.append(_sched("HPB-%04d" % eid, "HPBK-%04d" % b, 2, "cancelled",
                        clinic, "EX-2", day, "09:00", day, "10:00",
                        day + " 07:30:00"))
    eid += 1
    b += 1
    # zero-length effective BOOKED
    sched.append(_sched("HPB-%04d" % eid, "HPBK-%04d" % b, 1, "booked",
                        clinic, "EX-3", day, "10:00", day, "10:00",
                        day + " 06:00:00"))
    eid += 1
    b += 1
    # collision loser that remains a validated survivor (VOIDED)
    sched.append(_sched("HPB-%04d" % eid, "HPBK-%04d" % b, 1, "booked",
                        clinic, "EX-4", day, "09:00", day, "10:00",
                        day + " 06:00:00"))
    eid += 1
    adj.append(_adj("HPB-%04d" % eid, "HPBK-%04d" % b, 1, "voided",
                    clinic, "EX-4", day, "09:00", day, "10:00",
                    day + " 08:00:00"))
    eid += 1
    b += 1
    # superseded BOOKED revision (booked -> voided -> booked effective)
    sched.append(_sched("HPB-%04d" % eid, "HPBK-%04d" % b, 1, "booked",
                        clinic, "EX-5", day, "09:00", day, "10:00",
                        day + " 06:00:00"))
    eid += 1
    sched.append(_sched("HPB-%04d" % eid, "HPBK-%04d" % b, 2, "voided",
                        clinic, "EX-5", day, "09:00", day, "10:00",
                        day + " 07:00:00"))
    eid += 1
    sched.append(_sched("HPB-%04d" % eid, "HPBK-%04d" % b, 3, "booked",
                        clinic, "EX-5", day, "11:00", day, "12:00",
                        day + " 08:00:00"))
    eid += 1
    b += 1
    # effective BOOKED contributor
    sched.append(_sched("HPB-%04d" % eid, "HPBK-%04d" % b, 1, "booked",
                        clinic, "EX-6", day, "09:00", day, "10:00",
                        day + " 06:00:00"))
    eid += 1
    b += 1
    # VOIDED collision winner (higher created_ts VOIDED wins collision -> the
    # booking's effective falls back to a lower non-voided revision)
    sched.append(_sched("HPB-%04d" % eid, "HPBK-%04d" % b, 1, "booked",
                        clinic, "EX-7", day, "09:00", day, "10:00",
                        day + " 06:00:00"))
    eid += 1
    sched.append(_sched("HPB-%04d" % eid, "HPBK-%04d" % b, 2, "booked",
                        clinic, "EX-7", day, "10:00", day, "11:00",
                        day + " 07:00:00"))
    eid += 1
    adj.append(_adj("HPB-%04d" % eid, "HPBK-%04d" % b, 2, "voided",
                    clinic, "EX-7", day, "10:00", day, "11:00",
                    day + " 09:00:00"))
    eid += 1
    b += 1
    return sched, adj


# ---------------------------------------------------------------------------
# Family 7: COMPOSED
# ---------------------------------------------------------------------------

def _gen_composed(rng):
    sched = []
    adj = []
    eid = 7001
    day = "2026-10-05"
    b = 7001
    # Shared room-day: CLIN-E/EX-1. Bookings combine fresh ids, cross-source
    # dedup, identity reassignment, collision tiebreak, overlap, zero-length.
    # booking 1: within-feed duplicate on the shared room-day.
    sched.append(_sched("HCP-%04d" % eid, "HCBK-%04d" % b, 1, "booked",
                        "CLIN-E", "EX-1", day, "08:00", day, "12:00",
                        day + " 06:00:00"))
    eid += 1
    sched.append(_sched("HCP-%04d" % eid, "HCBK-%04d" % b, 1, "booked",
                        "CLIN-E", "EX-1", day, "08:00", day, "12:00",
                        day + " 06:00:00"))
    eid += 1
    b += 1
    # booking 2: identity reassignment rev1 CLIN-E, rev2 CLIN-F
    # (EXAM-NEW -> XRM-2).
    sched.append(_sched("HCP-%04d" % eid, "HCBK-%04d" % b, 1, "booked",
                        "CLIN-E", "EXAM-NEW", day, "09:00", day, "10:00",
                        day + " 06:10:00"))
    eid += 1
    sched.append(_sched("HCP-%04d" % eid, "HCBK-%04d" % b, 2, "booked",
                        "CLIN-F", "EXAM-NEW", day, "09:00", day, "10:00",
                        day + " 06:30:00"))
    eid += 1
    b += 1
    # booking 3: cross-source duplicate; scheduling copy first, adjustment
    # copy VOIDED (must be dropped as a duplicate).
    sched.append(_sched("HCP-%04d" % eid, "HCBK-%04d" % b, 1, "booked",
                        "CLIN-E", "EX-1", day, "10:00", day, "11:00",
                        day + " 06:20:00"))
    eid += 1
    adj.append(_adj("HCP-%04d" % eid, "HCBK-%04d" % b, 1, "voided",
                    "CLIN-E", "EX-1", day, "10:00", day, "11:00",
                    day + " 09:00:00"))
    eid += 1
    b += 1
    # booking 4: collision tie — same created_ts, greater event_id wins.
    sched.append(_sched("HCP-%04d" % eid, "HCBK-%04d" % b, 1, "cancelled",
                        "CLIN-E", "EX-1", day, "11:00", day, "12:00",
                        day + " 06:40:00"))
    eid += 1
    sched.append(_sched("HCP-%04d" % eid, "HCBK-%04d" % b, 1, "booked",
                        "CLIN-E", "EX-1", day, "11:00", day, "12:00",
                        day + " 06:40:00"))
    eid += 1
    b += 1
    # booking 5: zero-length effective BOOKED on the same room-day.
    sched.append(_sched("HCP-%04d" % eid, "HCBK-%04d" % b, 1, "booked",
                        "CLIN-E", "EX-1", day, "13:00", day, "13:00",
                        day + " 06:50:00"))
    eid += 1
    b += 1
    # booking 6: multi-day booking spanning the shared day.
    sched.append(_sched("HCP-%04d" % eid, "HCBK-%04d" % b, 1, "booked",
                        "CLIN-E", "EX-1", day, "14:00", "2026-10-06", "10:00",
                        day + " 07:00:00"))
    eid += 1
    b += 1
    # booking 7: all-voided booking on the same room-day (no contribution).
    sched.append(_sched("HCP-%04d" % eid, "HCBK-%04d" % b, 1, "voided",
                        "CLIN-E", "EX-1", day, "15:00", day, "16:00",
                        day + " 07:10:00"))
    eid += 1
    sched.append(_sched("HCP-%04d" % eid, "HCBK-%04d" % b, 2, "voided",
                        "CLIN-E", "EX-1", day, "15:00", day, "16:00",
                        day + " 07:20:00"))
    eid += 1
    b += 1
    return sched, adj


# ---------------------------------------------------------------------------
# Family 8: LARGE / MIXED SCALE
# ---------------------------------------------------------------------------

def _gen_large_scale(rng):
    sched = []
    adj = []
    eid = 8001
    bid = 8001
    clinics = ["CLIN-E", "CLIN-F", "CLIN-G", "CLIN-H"]
    rooms = ["EX-1", "EX-2", "EX-3", "EX-5", "EX-7", "EXAM-NEW", "XRM-8"]
    statuses = ["booked", "booked", "booked", "cancelled", "voided"]
    for i in range(600):
        clinic = rng.choice(clinics)
        room = rng.choice(rooms)
        day = "2026-11-%02d" % rng.randint(1, 20)
        st = "%02d:%02d" % (rng.randint(7, 17), rng.choice([0, 15, 30, 45]))
        eh = min(22, int(st[:2]) + rng.randint(1, 3))
        end_day = day if rng.random() > 0.05 else "2026-11-%02d" % rng.randint(
            int(day[-2:]), 28)
        end_day = day if end_day < day else end_day
        nrev = rng.choice([1, 1, 2, 3])
        top = rng.randint(1, 14)
        revs = sorted(rng.sample(range(1, 15), nrev))
        for rv in revs:
            status = statuses[rng.randrange(len(statuses))]
            if rv == nrev:
                status = rng.choice(["booked", "cancelled"])
            sched.append(_sched("HLG-%04d" % eid, "HLBK-%04d" % bid, rv,
                                status, clinic, room, day, st, end_day,
                                "%02d:%02d" % (eh, int(st[3:])),
                                "%s %02d:%02d:00" % (day, 5 + rv,
                                                     rng.randint(0, 40))))
            eid += 1
        if rng.random() < 0.06:
            adj.append(_adj("HLG-%04d" % eid, "HLBK-%04d" % bid, nrev,
                            rng.choice(statuses), clinic, room, day, st,
                            end_day, "%02d:%02d" % (eh, int(st[3:])),
                            "%s %02d:%02d:00" % (day, 5 + nrev + 1,
                                                 rng.randint(0, 40))))
            eid += 1
        bid += 1
    return sched, adj


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

GENERATORS = {
    "fresh_identities": _gen_fresh_identities,
    "source_order_dedup": _gen_source_order_dedup,
    "collision_topology": _gen_collision_topology,
    "identity_reassignment": _gen_identity_reassignment,
    "temporal_capacity": _gen_temporal_capacity,
    "population_boundaries": _gen_population_boundaries,
    "composed": _gen_composed,
    "large_scale": _gen_large_scale,
}

SEEDS = {
    "fresh_identities": 11001,
    "source_order_dedup": 11002,
    "collision_topology": 11003,
    "identity_reassignment": 11004,
    "temporal_capacity": 11005,
    "population_boundaries": 11006,
    "composed": 11007,
    "large_scale": 11008,
}


def generate(name):
    """Return dict with scheduling, adjustments, clinic, room data."""
    if name not in GENERATORS:
        raise KeyError(name)
    rng = random.Random(SEEDS[name])
    sched, adj = GENERATORS[name](rng)
    return {
        "scheduling": sched,
        "adjustments": adj,
        "clinic_directory": _clinic_directory(),
        "room_directory": _room_directory(),
    }
