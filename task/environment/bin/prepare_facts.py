#!/usr/bin/env python3
"""Fact materialization component for the room-capacity pipeline.

Derives the five output artifacts from the persisted canonical state and
writes them to the output directory.

Usage:
  python3 bin/prepare_facts.py --state /app/state/state.json \
      --output /app/output
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import state as state_mod

EFFECTIVE_COLS = ["booking_id", "status", "clinic_id", "room_id", "start_ts",
                  "end_ts", "provider_id", "source", "effective_revision"]
HISTORY_COLS = ["booking_id", "revision", "status", "clinic_id", "room_id",
                "start_ts", "end_ts", "effective_from", "effective_to",
                "is_effective"]
RECON_COLS = ["room_id", "clinic_id", "calendar_date",
              "effective_booking_count", "raw_interval_minutes",
              "blocked_minutes", "overlap_minutes"]
FACT_COLS = ["room_id", "clinic_id", "calendar_date", "blocked_minutes"]


def _day_segments(start_ts, end_ts):
    import datetime
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


def _record_lookup(state):
    return {r["event_id"]: r for r in state["canonical_events"]}


def _auth_effective(state, lookup):
    authoritative = {}
    for key, eid in state["authoritative"].items():
        bid, rev = key.split("|")
        authoritative[(bid, int(rev))] = lookup.get(eid)
    effective = {}
    for bid, eid in state["effective"].items():
        effective[bid] = lookup.get(eid) if eid else None
    return authoritative, effective


def _effective_booked(effective):
    return {bid: rec for bid, rec in effective.items()
            if rec is not None and rec["status"] == "BOOKED"}


def _replay_effective(state, lookup):
    """Reconstruct effective state from the persisted canonical ledger.

    The replay path walks the canonical events in their persisted ingestion
    order and, per booking, keeps the last non-VOIDED row encountered. This
    differs from a fresh rebuild, which selects the highest-numbered
    non-VOIDED authoritative revision.
    """
    authoritative = {}
    by_booking = defaultdict(list)
    for rec in state["canonical_events"]:
        key = (rec["booking_id"], rec["revision"])
        prior = authoritative.get(key)
        if prior is None or (rec["created_ts"], rec["event_id"]) > \
                (prior["created_ts"], prior["event_id"]):
            authoritative[key] = rec
        if rec is not None:
            by_booking[rec["booking_id"]].append(rec)
    effective = {}
    for bid, recs in by_booking.items():
        chosen = None
        for rec in recs:
            if rec["status"] != "VOIDED":
                chosen = rec
        effective[bid] = chosen
    return authoritative, effective


def build_artifacts(state, replay=False):
    lookup = _record_lookup(state)
    if replay:
        authoritative, effective = _replay_effective(state, lookup)
    else:
        authoritative, effective = _auth_effective(state, lookup)
    ledger = state["canonical_events"]

    # effective_bookings.csv
    eff_rows = []
    for bid in sorted(effective):
        rec = effective[bid]
        if rec is None:
            continue
        eff_rows.append([bid, rec["status"], rec["clinic_id"],
                         rec["room_id"], rec["start_ts"], rec["end_ts"],
                         rec["provider_id"], rec["source"],
                         str(rec["revision"])])

    # booking_state_history.csv
    by_booking = defaultdict(list)
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
                # Revision exists in the ledger but has no authoritative
                # winner in the persisted derived state (e.g., the state was
                # not rebuilt after a later ingest). Represent the row with
                # the ledger record so replay stays runnable.
                rec = next((x for x in ledger
                            if x["booking_id"] == bid and x["revision"] == rv),
                           None)
                if rec is None:
                    continue
                auth_by_rev[rv] = rec
            higher = [x for x in revisions if x > rv]
            nxt = min(higher) if higher else None
            eff_to = (auth_by_rev[nxt]["created_ts"]
                      if nxt is not None and nxt in auth_by_rev else "")
            hist_rows.append([
                bid, str(rv), rec["status"], rec["clinic_id"],
                rec["room_id"], rec["start_ts"], rec["end_ts"],
                rec["created_ts"], eff_to, "1" if eff_rev == rv else "0",
            ])
    hist_rows.sort(key=lambda r: (r[0], int(r[1])))

    # Compute the current day timelines from the effective BOOKED state, then
    # merge with any previously materialized room-days persisted in state.
    # A room-day that no longer has an effective contributor is NOT removed:
    # materialization is additive, so superseded contributions linger.
    current = defaultdict(list)
    for bid, rec in _effective_booked(effective).items():
        for day, lo, hi in _day_segments(rec["start_ts"], rec["end_ts"]):
            current[(rec["room_id"], rec["clinic_id"], day)].append((lo, hi))

    day_timelines = defaultdict(list)
    stale_minutes = {}  # (room,clinic,day) -> persisted stale minute total
    materialized = state.get("materialized", {})
    for key, minutes in materialized.items():
        room, clinic, day = key.split("|")
        tkey = (room, clinic, __import__("datetime").date.fromisoformat(day))
        if tkey not in current:
            stale_minutes[tkey] = minutes
    for key, intervals in current.items():
        day_timelines[key].extend(intervals)
    # Persist the additive materialized map: union of current room-days with
    # the previously persisted ones (never shrink).
    merged_materialized = dict(materialized)
    for (room, clinic, day), iv in current.items():
        key = "%s|%s|%s" % (room, clinic, day.isoformat())
        merged_materialized[key] = _union_minutes(iv)
    state["materialized"] = merged_materialized

    fact_rows = []
    for (room, clinic, day), iv in day_timelines.items():
        minutes = _union_minutes(iv)
        if minutes > 0:
            fact_rows.append([room, clinic, day.isoformat(), str(minutes)])
    for (room, clinic, day), minutes in stale_minutes.items():
        fact_rows.append([room, clinic, day.isoformat(), str(minutes)])
    fact_rows.sort(key=lambda r: (r[0], r[1], r[2]))

    recon_by_day = defaultdict(lambda: defaultdict(list))
    for bid, rec in _effective_booked(effective).items():
        for day, lo, hi in _day_segments(rec["start_ts"], rec["end_ts"]):
            recon_by_day[(rec["room_id"], rec["clinic_id"], day)][
                bid].append((lo, hi))
    stale_totals = {}
    for (room, clinic, day), minutes in stale_minutes.items():
        key = (room, clinic, day)
        stale_totals.setdefault(key, 0)
        stale_totals[key] = max(stale_totals[key], minutes)
    recon_rows = []
    for (room, clinic, day), contrib in recon_by_day.items():
        raw = sum(_union_minutes(iv) for iv in contrib.values())
        all_iv = [seg for iv in contrib.values() for seg in iv]
        blocked = _union_minutes(all_iv)
        stale = stale_totals.get((room, clinic, day), 0)
        if stale > 0:
            # Stale persisted contribution still counts on this room-day.
            raw = raw + stale
            blocked = blocked + stale
        recon_rows.append([
            room, clinic, day.isoformat(), str(len(contrib)), str(raw),
            str(blocked), str(raw - blocked),
        ])
    for (room, clinic, day), minutes in stale_totals.items():
        if (room, clinic, day) not in recon_by_day:
            recon_rows.append([
                room, clinic, day.isoformat(), "1", str(minutes),
                str(minutes), "0",
            ])
    recon_rows.sort(key=lambda r: (r[0], r[1], r[2]))

    # quality summary
    total_raw = sum(s["total_raw"] for s in state["sources"].values())
    duplicates = sum(s["duplicates"] for s in state["sources"].values())
    malformed = sum(s["malformed"] for s in state["sources"].values())
    adj_total = sum(s["adj_total"] for s in state["sources"].values())
    adj_valid = sum(1 for r in ledger if r["source"] == "ADJUSTMENT")
    clinic_resolved = sum(1 for r in ledger if r["clinic_hit"])
    room_resolved = sum(1 for r in ledger if r["room_hit"])
    unresolved = sum(1 for r in ledger
                     if not r["clinic_hit"] or not r["room_hit"])
    eff_list = list(effective.values())
    multi_day = sum(
        1 for rec in eff_list if rec is not None and rec["status"] == "BOOKED"
        and len(set(d for d, _lo, _hi in _day_segments(rec["start_ts"],
                                                       rec["end_ts"]))) > 1)
    total_blocked = sum(int(r[3]) for r in fact_rows)
    room_overlap = sum(1 for r in recon_rows if int(r[4]) > int(r[5]))
    overlap_total = sum(int(r[6]) for r in recon_rows)
    mismatches = sum(1 for r in recon_rows
                     if int(r[4]) != int(r[5]) + int(r[6]) or int(r[6]) < 0)
    summary = {
        "event_quality": {
            "total_raw_events": total_raw,
            "duplicate_events_removed": duplicates,
            "malformed_rows_rejected": malformed,
            "adjustment_rows_valid": adj_valid,
            "adjustment_rows_rejected": adj_total - adj_valid,
        },
        "identity_quality": {
            "clinic_aliases_resolved": clinic_resolved,
            "room_aliases_resolved": room_resolved,
            "identity_tokens_unresolved": unresolved,
        },
        "revision_quality": {
            "total_booking_ids": len(effective),
            "voided_revisions": sum(1 for r in ledger
                                    if r["status"] == "VOIDED"),
            "bookings_with_effective_revision":
                sum(1 for r in eff_list if r is not None),
            "active_bookings":
                sum(1 for r in eff_list
                    if r is not None and r["status"] == "BOOKED"),
            "cancelled_bookings":
                sum(1 for r in eff_list
                    if r is not None and r["status"] == "CANCELLED"),
            "bookings_with_no_effective_revision":
                sum(1 for r in eff_list if r is None),
            "multi_day_active_bookings": multi_day,
        },
        "capacity_quality": {
            "total_blocked_minutes": total_blocked,
            "room_clinic_days_with_overlap": room_overlap,
            "overlap_minutes_total": overlap_total,
        },
        "reconciliation_quality": {
            "reconciled_room_days": len(recon_rows),
            "reconciliation_mismatches": mismatches,
        },
    }
    return {
        "effective_bookings.csv": eff_rows,
        "booking_state_history.csv": hist_rows,
        "daily_room_capacity_facts.csv": fact_rows,
        "capacity_reconciliation.csv": recon_rows,
        "quality_summary.json": summary,
    }


def _write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as h:
        writer = csv.writer(h, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow(row)


def prepare(state_path, output_dir, replay=False, write=True):
    state = state_mod.load_state(state_path)
    artifacts = build_artifacts(state, replay=replay)
    if not write:
        return artifacts
    os.makedirs(output_dir, exist_ok=True)
    _write_csv(os.path.join(output_dir, "effective_bookings.csv"),
               EFFECTIVE_COLS, artifacts["effective_bookings.csv"])
    _write_csv(os.path.join(output_dir, "booking_state_history.csv"),
               HISTORY_COLS, artifacts["booking_state_history.csv"])
    _write_csv(os.path.join(output_dir, "daily_room_capacity_facts.csv"),
               FACT_COLS, artifacts["daily_room_capacity_facts.csv"])
    _write_csv(os.path.join(output_dir, "capacity_reconciliation.csv"),
               RECON_COLS, artifacts["capacity_reconciliation.csv"])
    with open(os.path.join(output_dir, "quality_summary.json"), "w",
              encoding="utf-8") as h:
        json.dump(artifacts["quality_summary.json"], h, indent=2)
        h.write("\n")
    state_mod.save_state(state_path, state)
    return artifacts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default=state_mod.default_state_path())
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--replay", action="store_true",
                        help="reconstruct from persisted authoritative state")
    args = parser.parse_args()
    redirect = bool(os.environ.get("DYNAMO_EFFECTIVE_PATH"))
    artifacts = prepare(args.state, args.output, replay=args.replay,
                        write=not redirect)
    if redirect:
        out_dir = os.path.dirname(os.environ["DYNAMO_EFFECTIVE_PATH"])
        os.makedirs(out_dir, exist_ok=True)
        _write_csv(os.environ["DYNAMO_EFFECTIVE_PATH"], EFFECTIVE_COLS,
                   artifacts["effective_bookings.csv"])
        _write_csv(os.environ.get("DYNAMO_HISTORY_PATH",
                                  os.path.join(out_dir,
                                               "booking_state_history.csv")),
                   HISTORY_COLS, artifacts["booking_state_history.csv"])
        _write_csv(os.environ.get("DYNAMO_FACTS_PATH",
                                  os.path.join(out_dir,
                                               "daily_room_capacity_facts.csv")),
                   FACT_COLS, artifacts["daily_room_capacity_facts.csv"])
        _write_csv(os.environ.get("DYNAMO_RECONCILIATION_PATH",
                                  os.path.join(out_dir,
                                               "capacity_reconciliation.csv")),
                   RECON_COLS, artifacts["capacity_reconciliation.csv"])
        with open(os.environ.get("DYNAMO_SUMMARY_PATH",
                                 os.path.join(out_dir,
                                              "quality_summary.json")), "w",
                  encoding="utf-8") as h:
            json.dump(artifacts["quality_summary.json"], h, indent=2)
            h.write("\n")
    print("artifacts=%d" % sum(
        len(v) if isinstance(v, list) else 1
        for v in artifacts.values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
