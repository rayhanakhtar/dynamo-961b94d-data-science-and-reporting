"""Shared fixtures for the multi-artifact verifier.

The verifier reads only:
  - public inputs (/app/data/*),
  - the agent's outputs (/app/output/*),
  - hidden golden expected outputs (/tests/expected/*).

It NEVER imports or executes the agent's prepare_facts.py or any solution
module. The independent semantic recomputation below is verifier-side code
written against the contract; it is intentionally a separate implementation
from the starter and from the reference oracle.
"""

import csv
import json
import os
import datetime
import collections

import pytest

OUTPUT_DIR = "/app/output"
DATA_INPUT_SCHEDULING = os.environ.get(
    "DYNAMO_INPUT_SCHEDULING", "/app/data/scheduling_events.csv")
DATA_INPUT_ADJUSTMENTS = os.environ.get(
    "DYNAMO_INPUT_ADJUSTMENTS", "/app/data/reservation_adjustments.ndjson")
DATA_INPUT_CLINICS = os.environ.get(
    "DYNAMO_INPUT_CLINICS", "/app/data/clinic_directory.csv")
DATA_INPUT_ROOMS = os.environ.get(
    "DYNAMO_INPUT_ROOMS", "/app/data/room_directory.csv")

EXPECTED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "expected")

EFFECTIVE_PATH = os.environ.get(
    "DYNAMO_EFFECTIVE_PATH",
    os.path.join(OUTPUT_DIR, "effective_bookings.csv"))
HISTORY_PATH = os.environ.get(
    "DYNAMO_HISTORY_PATH",
    os.path.join(OUTPUT_DIR, "booking_state_history.csv"))
FACTS_PATH = os.environ.get(
    "DYNAMO_FACTS_PATH",
    os.path.join(OUTPUT_DIR, "daily_room_capacity_facts.csv"))
RECONCILIATION_PATH = os.environ.get(
    "DYNAMO_RECONCILIATION_PATH",
    os.path.join(OUTPUT_DIR, "capacity_reconciliation.csv"))
SUMMARY_PATH = os.environ.get(
    "DYNAMO_SUMMARY_PATH",
    os.path.join(OUTPUT_DIR, "quality_summary.json"))

EFFECTIVE_COLS = ["booking_id", "status", "clinic_id", "room_id",
                  "start_ts", "end_ts", "provider_id", "source",
                  "effective_revision"]
HISTORY_COLS = ["booking_id", "revision", "status", "clinic_id", "room_id",
                "start_ts", "end_ts", "effective_from", "effective_to",
                "is_effective"]
FACTS_COLS = ["room_id", "clinic_id", "calendar_date", "blocked_minutes"]
RECONCILIATION_COLS = ["room_id", "clinic_id", "calendar_date",
                       "effective_booking_count", "raw_interval_minutes",
                       "blocked_minutes", "overlap_minutes"]


# ---------------------------------------------------------------------------
# Artifact readers
# ---------------------------------------------------------------------------

def read_csv(path):
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        rows = list(reader)
    return header, rows


def read_lookup(rows):
    lookup = {}
    for row in rows:
        key = tuple(row[:len(row) - 1])
        lookup[key] = row[-1]
    return lookup


def read_summary(path):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    seen = {}

    def pairs_hook(pairs):
        for key, _value in pairs:
            seen[key] = seen.get(key, 0) + 1
        return dict(pairs)

    summary = json.loads(text, object_pairs_hook=pairs_hook)
    duplicates = {key for key, count in seen.items() if count > 1}
    return summary, duplicates


@pytest.fixture(scope="module")
def effective_artifact():
    header, rows = read_csv(EFFECTIVE_PATH)
    return header, rows


@pytest.fixture(scope="module")
def history_artifact():
    header, rows = read_csv(HISTORY_PATH)
    return header, rows


@pytest.fixture(scope="module")
def facts_artifact():
    header, rows = read_csv(FACTS_PATH)
    return header, rows


@pytest.fixture(scope="module")
def reconciliation_artifact():
    header, rows = read_csv(RECONCILIATION_PATH)
    return header, rows


@pytest.fixture(scope="module")
def summary_artifact():
    return read_summary(SUMMARY_PATH)


# ---------------------------------------------------------------------------
# Independent verifier-side recomputation (contract semantics)
# ---------------------------------------------------------------------------

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
    """Normalize one raw event row; return a record dict or None if malformed."""
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
        "feed": raw.get("feed", ""),
        "created_ts": created_ts, "start": start, "end": end,
    }


def _load_public_rows():
    """Return (raw_sched, raw_adj, clinic_map, room_map) from public inputs."""
    raw_sched = []
    if os.path.exists(DATA_INPUT_SCHEDULING):
        with open(DATA_INPUT_SCHEDULING, encoding="utf-8", newline="") as h:
            raw_sched = [dict(r, feed="SCHEDULING")
                         for r in csv.DictReader(h)]
    raw_adj = []
    if os.path.exists(DATA_INPUT_ADJUSTMENTS):
        with open(DATA_INPUT_ADJUSTMENTS, encoding="utf-8") as h:
            for line in h:
                line = line.strip()
                if line:
                    raw_adj.append(dict(json.loads(line), feed="ADJUSTMENT"))

    clinic_map = {}
    if os.path.exists(DATA_INPUT_CLINICS):
        with open(DATA_INPUT_CLINICS, encoding="utf-8", newline="") as h:
            for r in csv.DictReader(h):
                clinic_map[r["clinic_alias"].strip().upper()] = \
                    r["canonical_clinic_id"].strip().upper()
    room_map = {}
    if os.path.exists(DATA_INPUT_ROOMS):
        with open(DATA_INPUT_ROOMS, encoding="utf-8", newline="") as h:
            for r in csv.DictReader(h):
                room_map[(r["clinic_id"].strip().upper(),
                          r["room_alias"].strip().upper())] = \
                    r["canonical_room_id"].strip().upper()
    return raw_sched, raw_adj, clinic_map, room_map


def _independent_pipeline():
    """Independently recompute the full pipeline from public inputs."""
    raw_sched, raw_adj, clinic_map, room_map = _load_public_rows()
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


@pytest.fixture(scope="module")
def indep():
    return _independent_pipeline()


# ---------------------------------------------------------------------------
# Independent derivation helpers for facts / reconciliation / history
# ---------------------------------------------------------------------------

def indep_day_segments(start_ts, end_ts):
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


def indep_union_minutes(intervals):
    merged = []
    for s, e in sorted(intervals):
        if merged and s < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return sum(int((e - s).total_seconds()) // 60 for s, e in merged)


def indep_effective_rows(effective):
    rows = {}
    for bid in sorted(effective):
        rec = effective[bid]
        if rec is None:
            continue
        rows[bid] = {
            "booking_id": bid, "status": rec["status"],
            "clinic_id": rec["clinic_id"], "room_id": rec["room_id"],
            "start_ts": rec["start_ts"], "end_ts": rec["end_ts"],
            "provider_id": rec["provider_id"], "source": rec["source"],
            "effective_revision": str(rec["revision"]),
        }
    return rows


def indep_facts_lookup(effective):
    day_timelines = collections.defaultdict(list)
    for rec in effective.values():
        if rec is None or rec["status"] != "BOOKED":
            continue
        for day, lo, hi in indep_day_segments(rec["start_ts"], rec["end_ts"]):
            day_timelines[(rec["room_id"], rec["clinic_id"],
                           day)].append((lo, hi))
    out = {}
    for (room, clinic, day), iv in day_timelines.items():
        minutes = indep_union_minutes(iv)
        if minutes > 0:
            out[(room, clinic, day.isoformat())] = minutes
    return out


def indep_reconciliation(effective):
    by_day = collections.defaultdict(lambda: collections.defaultdict(list))
    for rec in effective.values():
        if rec is None or rec["status"] != "BOOKED":
            continue
        for day, lo, hi in indep_day_segments(rec["start_ts"], rec["end_ts"]):
            by_day[(rec["room_id"], rec["clinic_id"], day)][
                rec["booking_id"]].append((lo, hi))
    rows = {}
    for (room, clinic, day), contrib in by_day.items():
        raw = sum(indep_union_minutes(iv) for iv in contrib.values())
        all_iv = [seg for iv in contrib.values() for seg in iv]
        blocked = indep_union_minutes(all_iv)
        rows[(room, clinic, day.isoformat())] = {
            "effective_booking_count": len(contrib),
            "raw_interval_minutes": raw,
            "blocked_minutes": blocked,
            "overlap_minutes": raw - blocked,
        }
    return rows


def indep_history(effective, authoritative, ledger):
    by_booking = collections.defaultdict(list)
    for rec in ledger:
        by_booking[rec["booking_id"]].append(rec["revision"])
    rows = {}
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
            rows[(bid, str(rv))] = {
                "booking_id": bid, "revision": str(rv), "status": rec["status"],
                "clinic_id": rec["clinic_id"], "room_id": rec["room_id"],
                "start_ts": rec["start_ts"], "end_ts": rec["end_ts"],
                "effective_from": rec["created_ts"], "effective_to": eff_to,
                "is_effective": "1" if eff_rev == rv else "0",
            }
    return rows


# ---------------------------------------------------------------------------
# Anti-cheat / output-path checks
# ---------------------------------------------------------------------------

OUTPUT_FILES = ["daily_room_capacity_facts.csv", "quality_summary.json",
                "effective_bookings.csv", "booking_state_history.csv",
                "capacity_reconciliation.csv"]


def check_output_files():
    for name in OUTPUT_FILES:
        path = os.path.join(OUTPUT_DIR, name)
        assert os.path.exists(path), "missing %s" % path
        assert not os.path.islink(path), "%s is a symlink" % path
        assert os.path.isfile(path), "%s is not a regular file" % path
        assert os.path.realpath(path).startswith(
            os.path.realpath(OUTPUT_DIR) + os.sep), \
            "%s resolves outside %s" % (path, OUTPUT_DIR)
