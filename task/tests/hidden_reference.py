"""Independent expected-artifact derivation for hidden input universes.

This mirrors the verifier-side independent recomputation in conftest.py but is
parameterized by explicit input paths, so the hidden grader can compute the
canonical five artifacts for any input universe without running the submitted
implementation. It deliberately re-implements the documented pipeline
independently of both the starter and the oracle.

Only the semantics documented in fact_contract.md are used.
"""

import collections
import csv
import datetime
import json

VALID_STATUSES = {"BOOKED", "CANCELLED", "VOIDED"}


def _parse_clock(text):
    parts = text.strip().split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        hh, mm = int(parts[0]), int(parts[1])
        ss = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        return None
    return hh * 60 + mm


def _normalize_event(raw):
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
        sd = datetime.date.fromisoformat(raw["start_date"].strip())
        ed = datetime.date.fromisoformat(raw["end_date"].strip())
        sm = _parse_clock(raw["start_time"])
        em = _parse_clock(raw["end_time"])
        created_ts = raw["created_ts"].strip()
        datetime.datetime.strptime(created_ts, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None
    if sm is None or em is None:
        return None
    start = datetime.datetime.combine(sd, datetime.time()) + \
        datetime.timedelta(minutes=sm)
    end = datetime.datetime.combine(ed, datetime.time()) + \
        datetime.timedelta(minutes=em)
    if end < start:
        return None
    return {
        "event_id": event_id, "booking_id": booking_id, "revision": revision,
        "status": status, "clinic_token": clinic_token,
        "room_token": room_token,
        "provider_id": (raw.get("provider_id") or "").strip().upper(),
        "source": (raw.get("source") or "").strip().upper(),
        "created_ts": created_ts, "start": start, "end": end,
    }


def _load(inputs_dir):
    sched_path = inputs_dir + "/scheduling_events.csv"
    adj_path = inputs_dir + "/reservation_adjustments.ndjson"
    clinic_path = inputs_dir + "/clinic_directory.csv"
    room_path = inputs_dir + "/room_directory.csv"

    raw_sched = []
    with open(sched_path, encoding="utf-8", newline="") as h:
        raw_sched = [dict(r, feed="SCHEDULING") for r in csv.DictReader(h)]
    raw_adj = []
    with open(adj_path, encoding="utf-8") as h:
        for line in h:
            line = line.strip()
            if line:
                raw_adj.append(dict(json.loads(line), feed="ADJUSTMENT"))
    clinic_map = {}
    with open(clinic_path, encoding="utf-8", newline="") as h:
        for r in csv.DictReader(h):
            clinic_map[r["clinic_alias"].strip().upper()] = \
                r["canonical_clinic_id"].strip().upper()
    room_map = {}
    with open(room_path, encoding="utf-8", newline="") as h:
        for r in csv.DictReader(h):
            room_map[(r["clinic_id"].strip().upper(),
                      r["room_alias"].strip().upper())] = \
                r["canonical_room_id"].strip().upper()
    return raw_sched, raw_adj, clinic_map, room_map


def _independent_pipeline(inputs_dir):
    raw_sched, raw_adj, clinic_map, room_map = _load(inputs_dir)
    combined = raw_sched + raw_adj
    total_raw = len(combined)

    seen = set()
    surviving = []
    duplicates = 0
    for row in combined:
        eid = row["event_id"].strip()
        if eid in seen:
            duplicates += 1
            continue
        seen.add(eid)
        surviving.append(row)

    records = []
    malformed = 0
    for row in surviving:
        rec = _normalize_event(row)
        if rec is None:
            malformed += 1
        else:
            records.append(rec)

    ledger = []
    clinic_resolved = 0
    room_resolved = 0
    unresolved = 0
    for rec in records:
        canonical_clinic = clinic_map.get(rec["clinic_token"],
                                          rec["clinic_token"])
        canonical_room = room_map.get((canonical_clinic, rec["room_token"]),
                                      rec["room_token"])
        clinic_hit = rec["clinic_token"] in clinic_map
        room_hit = (canonical_clinic, rec["room_token"]) in room_map
        if clinic_hit:
            clinic_resolved += 1
        if room_hit:
            room_resolved += 1
        if not (clinic_hit and room_hit):
            unresolved += 1
        ledger.append({
            "event_id": rec["event_id"], "booking_id": rec["booking_id"],
            "revision": rec["revision"], "status": rec["status"],
            "clinic_id": canonical_clinic, "room_id": canonical_room,
            "provider_id": rec["provider_id"], "source": rec["source"],
            "start_ts": rec["start"].strftime("%Y-%m-%d %H:%M:%S"),
            "end_ts": rec["end"].strftime("%Y-%m-%d %H:%M:%S"),
            "created_ts": rec["created_ts"],
        })
    ledger.sort(key=lambda r: r["event_id"])

    identity_stats = {
        "clinic_aliases_resolved": clinic_resolved,
        "room_aliases_resolved": room_resolved,
        "identity_tokens_unresolved": unresolved,
    }

    authoritative = {}
    for rec in ledger:
        key = (rec["booking_id"], rec["revision"])
        prior = authoritative.get(key)
        if prior is None or (rec["created_ts"], rec["event_id"]) > \
                (prior["created_ts"], prior["event_id"]):
            authoritative[key] = rec

    by_booking = collections.defaultdict(list)
    for rec in ledger:
        by_booking[rec["booking_id"]].append(rec["revision"])

    effective = {}
    for booking_id, revisions in by_booking.items():
        chosen = None
        for revision in sorted(set(revisions), reverse=True):
            cand = authoritative.get((booking_id, revision))
            if cand is not None and cand["status"] != "VOIDED":
                chosen = cand
                break
        effective[booking_id] = chosen

    return {
        "total_raw": total_raw, "duplicates": duplicates,
        "malformed": malformed, "records": records, "ledger": ledger,
        "authoritative": authoritative, "effective": effective,
        "identity_stats": identity_stats, "raw_adj_count": len(raw_adj),
    }


def _day_segments(start_ts, end_ts):
    start = datetime.datetime.strptime(start_ts, "%Y-%m-%d %H:%M:%S")
    end = datetime.datetime.strptime(end_ts, "%Y-%m-%d %H:%M:%S")
    day_start = datetime.datetime.combine(start.date(), datetime.time())
    while day_start < end:
        day_end = day_start + datetime.timedelta(days=1)
        lo = start if start > day_start else day_start
        hi = end if end < day_end else day_end
        if hi > lo:
            yield day_start.date(), lo, hi
        day_start = day_end


def _union_minutes(intervals):
    merged = []
    for s, e in sorted(intervals):
        if merged and s < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return sum(int((e - s).total_seconds()) // 60 for s, e in merged)


def _expected(inputs_dir):
    """Return the canonical five artifacts as serializable structures."""
    indep = _independent_pipeline(inputs_dir)
    effective = indep["effective"]

    # effective_bookings.csv
    eff_rows = []
    for bid in sorted(effective):
        rec = effective[bid]
        if rec is None:
            continue
        eff_rows.append([
            bid, rec["status"], rec["clinic_id"], rec["room_id"],
            rec["start_ts"], rec["end_ts"], rec["provider_id"],
            rec["source"], str(rec["revision"]),
        ])

    # booking_state_history.csv
    ledger = indep["ledger"]
    authoritative = indep["authoritative"]
    by_booking = collections.defaultdict(list)
    for rec in ledger:
        by_booking[rec["booking_id"]].append(rec["revision"])
    hist_rows = []
    for bid in sorted(by_booking):
        revisions = sorted(set(by_booking[bid]))
        auth_by_rev = {}
        for rv in revisions:
            rec = authoritative.get((bid, rv))
            if rec is not None:
                auth_by_rev[rv] = rec
        eff_rec = effective.get(bid)
        eff_rev = None if eff_rec is None else eff_rec["revision"]
        for rv in revisions:
            rec = auth_by_rev.get(rv)
            if rec is None:
                continue
            higher = [x for x in revisions if x > rv]
            nxt = min(higher) if higher else None
            eff_to = auth_by_rev[nxt]["created_ts"] if nxt is not None else ""
            hist_rows.append([
                bid, str(rv), rec["status"], rec["clinic_id"],
                rec["room_id"], rec["start_ts"], rec["end_ts"],
                rec["created_ts"], eff_to,
                "1" if eff_rev == rv else "0",
            ])
    hist_rows.sort(key=lambda r: (r[0], int(r[1])))

    # daily_room_capacity_facts.csv + capacity_reconciliation.csv
    day_timelines = collections.defaultdict(list)
    for rec in effective.values():
        if rec is None or rec["status"] != "BOOKED":
            continue
        for day, lo, hi in _day_segments(rec["start_ts"], rec["end_ts"]):
            day_timelines[(rec["room_id"], rec["clinic_id"], day)].append(
                (lo, hi))
    fact_rows = []
    for (room, clinic, day), iv in day_timelines.items():
        minutes = _union_minutes(iv)
        if minutes > 0:
            fact_rows.append([room, clinic, day.isoformat(), str(minutes)])
    fact_rows.sort(key=lambda r: (r[0], r[1], r[2]))

    recon_by_day = collections.defaultdict(lambda: collections.defaultdict(list))
    for rec in effective.values():
        if rec is None or rec["status"] != "BOOKED":
            continue
        for day, lo, hi in _day_segments(rec["start_ts"], rec["end_ts"]):
            recon_by_day[(rec["room_id"], rec["clinic_id"], day)][
                rec["booking_id"]].append((lo, hi))
    recon_rows = []
    for (room, clinic, day), contrib in recon_by_day.items():
        raw = sum(_union_minutes(iv) for iv in contrib.values())
        all_iv = [seg for iv in contrib.values() for seg in iv]
        blocked = _union_minutes(all_iv)
        recon_rows.append([
            room, clinic, day.isoformat(), str(len(contrib)), str(raw),
            str(blocked), str(raw - blocked),
        ])
    recon_rows.sort(key=lambda r: (r[0], r[1], r[2]))

    # quality_summary.json
    eff_list = list(effective.values())
    adj_valid = sum(1 for rec in indep["records"]
                    if rec["source"] == "ADJUSTMENT")
    event_quality = {
        "total_raw_events": indep["total_raw"],
        "duplicate_events_removed": indep["duplicates"],
        "malformed_rows_rejected": indep["malformed"],
        "adjustment_rows_valid": adj_valid,
        "adjustment_rows_rejected": indep["raw_adj_count"] - adj_valid,
    }
    identity_quality = dict(indep["identity_stats"])
    multi_day = sum(
        1 for rec in eff_list if rec is not None and rec["status"] == "BOOKED"
        and len(set(d for d, _lo, _hi in _day_segments(rec["start_ts"],
                                                       rec["end_ts"]))) > 1)
    revision_quality = {
        "total_booking_ids": len(effective),
        "voided_revisions": sum(1 for rec in indep["records"]
                                if rec["status"] == "VOIDED"),
        "bookings_with_effective_revision":
            sum(1 for rec in eff_list if rec is not None),
        "active_bookings":
            sum(1 for rec in eff_list
                if rec is not None and rec["status"] == "BOOKED"),
        "cancelled_bookings":
            sum(1 for rec in eff_list
                if rec is not None and rec["status"] == "CANCELLED"),
        "bookings_with_no_effective_revision":
            sum(1 for rec in eff_list if rec is None),
        "multi_day_active_bookings": multi_day,
    }
    total_blocked = sum(int(r[3]) for r in fact_rows)
    room_overlap = sum(1 for r in recon_rows if int(r[4]) > int(r[5]))
    overlap_total = sum(int(r[6]) for r in recon_rows)
    mismatches = sum(1 for r in recon_rows
                     if int(r[4]) != int(r[5]) + int(r[6]) or int(r[6]) < 0)
    capacity_quality = {
        "total_blocked_minutes": total_blocked,
        "room_clinic_days_with_overlap": room_overlap,
        "overlap_minutes_total": overlap_total,
    }
    reconciliation_quality = {
        "reconciled_room_days": len(recon_rows),
        "reconciliation_mismatches": mismatches,
    }
    summary = {
        "event_quality": event_quality,
        "identity_quality": identity_quality,
        "revision_quality": revision_quality,
        "capacity_quality": capacity_quality,
        "reconciliation_quality": reconciliation_quality,
    }

    return {
        "effective_bookings.csv": eff_rows,
        "booking_state_history.csv": hist_rows,
        "daily_room_capacity_facts.csv": fact_rows,
        "capacity_reconciliation.csv": recon_rows,
        "quality_summary.json": summary,
    }
