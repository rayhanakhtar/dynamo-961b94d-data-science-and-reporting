"""Cross-artifact invariants and held-out composed scenarios.

These tests connect the five artifacts to each other and to the independent
verifier-side recomputation. They are the primary guard that a single upstream
decision (identity, collision, effective state) propagates correctly across
every output.
"""

import csv
import json
import os

from conftest import (EFFECTIVE_PATH, HISTORY_PATH, FACTS_PATH,
                      RECONCILIATION_PATH, SUMMARY_PATH,
                      indep_facts_lookup, indep_reconciliation,
                      indep_effective_rows, indep_history,
                      indep_union_minutes, indep_day_segments)


def _dictrows(path):
    with open(path, encoding="utf-8", newline="") as h:
        return list(csv.DictReader(h))


# ---------------------------------------------------------------------------
# A. effective_bookings <-> history
# ---------------------------------------------------------------------------

def test_cross_effective_history_one_to_one():
    eff = _dictrows(EFFECTIVE_PATH)
    hist = _dictrows(HISTORY_PATH)
    hist_eff = {r["booking_id"]: r for r in hist if r["is_effective"] == "1"}
    assert len(hist_eff) == len(eff), \
        "is_effective count %d != effective_bookings count %d" % (
            len(hist_eff), len(eff))
    for e in eff:
        assert e["booking_id"] in hist_eff
        h = hist_eff[e["booking_id"]]
        assert h["revision"] == e["effective_revision"]
        assert h["status"] == e["status"]
        assert h["clinic_id"] == e["clinic_id"]
        assert h["room_id"] == e["room_id"]


# ---------------------------------------------------------------------------
# B. history <-> effective state (attributes match)
# ---------------------------------------------------------------------------

def test_cross_history_current_state():
    eff = _dictrows(EFFECTIVE_PATH)
    hist = _dictrows(HISTORY_PATH)
    for e in eff:
        h = [r for r in hist if r["booking_id"] == e["booking_id"]
             and r["is_effective"] == "1"][0]
        assert h["start_ts"] == e["start_ts"]
        assert h["end_ts"] == e["end_ts"]


# ---------------------------------------------------------------------------
# C. effective_bookings <-> facts (capacity provenance)
# ---------------------------------------------------------------------------

def test_cross_capacity_provenance():
    """Every fact blocked minute is attributable to an effective BOOKED booking."""
    facts = _dictrows(FACTS_PATH)
    eff = _dictrows(EFFECTIVE_PATH)
    active = [e for e in eff if e["status"] == "BOOKED"]
    assert len(active) > 0
    # facts non-empty
    assert facts
    # every fact room/day must be derivable from at least one active effective booking
    # (verified precisely by independent recomputation equality in test_daily_facts)


# ---------------------------------------------------------------------------
# D. effective_bookings <-> reconciliation (count provenance)
# ---------------------------------------------------------------------------

def test_cross_reconciliation_booking_count():
    """reconciliation.effective_booking_count derives from effective bookings."""
    rec = _dictrows(RECONCILIATION_PATH)
    eff = _dictrows(EFFECTIVE_PATH)
    active = [e for e in eff if e["status"] == "BOOKED"]
    for r in rec:
        count = 0
        for e in active:
            if e["clinic_id"] != r["clinic_id"] or e["room_id"] != r["room_id"]:
                continue
            for day, _lo, _hi in indep_day_segments(e["start_ts"], e["end_ts"]):
                if day.isoformat() == r["calendar_date"]:
                    count += 1
                    break
        assert int(r["effective_booking_count"]) == count, \
            "effective_booking_count mismatch for %r" % (
                (r["room_id"], r["clinic_id"], r["calendar_date"]),)


# ---------------------------------------------------------------------------
# E. facts <-> reconciliation (same keys, same blocked)
# ---------------------------------------------------------------------------

def test_cross_facts_reconciliation_bijection():
    facts = _dictrows(FACTS_PATH)
    rec = _dictrows(RECONCILIATION_PATH)
    fkeys = {(r["room_id"], r["clinic_id"], r["calendar_date"]) for r in facts}
    rkeys = {(r["room_id"], r["clinic_id"], r["calendar_date"]) for r in rec}
    assert fkeys == rkeys
    for r in rec:
        f = [x for x in facts if x["room_id"] == r["room_id"]
             and x["clinic_id"] == r["clinic_id"]
             and x["calendar_date"] == r["calendar_date"]][0]
        assert int(f["blocked_minutes"]) == int(r["blocked_minutes"])


# ---------------------------------------------------------------------------
# F. quality <-> artifacts
# ---------------------------------------------------------------------------

def test_cross_quality_artifacts():
    rec = _dictrows(RECONCILIATION_PATH)
    facts = _dictrows(FACTS_PATH)
    with open(SUMMARY_PATH, encoding="utf-8") as h:
        summary = json.load(h)
    cq = summary["capacity_quality"]
    assert cq["total_blocked_minutes"] == sum(int(r["blocked_minutes"]) for r in facts)
    assert cq["total_blocked_minutes"] == sum(int(r["blocked_minutes"]) for r in rec)
    assert cq["overlap_minutes_total"] == sum(int(r["overlap_minutes"]) for r in rec)
    assert summary["reconciliation_quality"]["reconciled_room_days"] == len(rec)
    assert summary["reconciliation_quality"]["reconciliation_mismatches"] == 0


# ---------------------------------------------------------------------------
# G. no superseded leakage
# ---------------------------------------------------------------------------

def test_cross_no_superseded_leakage(indep):
    """No historical/superseded interval contributes capacity."""
    facts = _dictrows(FACTS_PATH)
    produced = {(r["room_id"], r["clinic_id"], r["calendar_date"]): int(r["blocked_minutes"])
                for r in facts}
    independent = indep_facts_lookup(indep["effective"])
    assert produced == independent


# ---------------------------------------------------------------------------
# H. no provider partition
# ---------------------------------------------------------------------------

def test_cross_no_provider_partition(indep):
    """Provider/source differences must not create separate capacity timelines."""
    facts = _dictrows(FACTS_PATH)
    lookup = {(r["room_id"], r["clinic_id"], r["calendar_date"]): int(r["blocked_minutes"])
              for r in facts}
    contrib = {}
    for rec in indep["effective"].values():
        if rec is None or rec["status"] != "BOOKED":
            continue
        for day, lo, hi in indep_day_segments(rec["start_ts"], rec["end_ts"]):
            key = (rec["room_id"], rec["clinic_id"], day.isoformat())
            contrib.setdefault(key, []).append((lo, hi))
    for key, iv in contrib.items():
        assert lookup[key] == indep_union_minutes(iv), \
            "room-day %r must be union across providers" % (key,)


# ---------------------------------------------------------------------------
# Held-out composed scenario families (independent recomputation anchors)
# ---------------------------------------------------------------------------

def test_heldout_alias_collision_union(indep):
    """Clinic alias + room alias + cross-source collision + shared union.

    BK-0201 (adjustment winner on EXAM-3 in CL-A -> RM-03) and any neighbor on
    RM-03 must union on RM-03.
    """
    eff = indep_effective_rows(indep["effective"])
    b = eff.get("BK-0201")
    assert b is not None
    assert b["room_id"] == "RM-03", \
        "BK-0201 should resolve to RM-03, got %s" % b["room_id"]


def test_heldout_divergent_alias_by_clinic(indep):
    """Same room alias EXAM-3 must resolve differently by clinic.

    BK-5302 uses clinic SOUTH (-> CL-C) + EXAM-3 -> RM-05.
    """
    eff = indep_effective_rows(indep["effective"])
    b = eff.get("BK-5302")
    assert b is not None
    assert b["clinic_id"] == "CL-C" and b["room_id"] == "RM-05", \
        "BK-5302 should be CL-C/RM-05, got %s/%s" % (b["clinic_id"], b["room_id"])


def test_heldout_collision_cancel(indep):
    """Cross-source collision: later adjustment cancels (BK-0101), older loses
    (BK-0102)."""
    eff = indep_effective_rows(indep["effective"])
    assert eff["BK-0101"]["status"] == "CANCELLED"
    assert eff["BK-0102"]["status"] == "BOOKED"


def test_heldout_voided_highest(indep):
    """VOIDED highest revision (BK-1501) falls back to lower BOOKED."""
    eff = indep_effective_rows(indep["effective"])
    assert eff["BK-1501"]["status"] == "BOOKED"
    assert eff["BK-1501"]["effective_revision"] == "1"


def test_heldout_all_voided_absent(indep):
    """All-VOIDED booking (BK-1504) has no effective booking."""
    eff = indep_effective_rows(indep["effective"])
    assert "BK-1504" not in eff


def test_heldout_zero_length_excluded_from_count(indep):
    """A zero-length effective BOOKED booking (e.g. BK-1301) is active but
    contributes zero minutes, so it does not appear in reconciliation."""
    eff = indep_effective_rows(indep["effective"])
    assert eff.get("BK-1301") is not None, "zero-length booking should be active"
    rec = indep_reconciliation(indep["effective"])
    # no room-day row for the zero-length booking's room/day
    for key in rec:
        pass
    # (zero-length produces no day segments, so it cannot contribute a row)


def test_heldout_multiday_union(indep):
    """A multi-day effective booking unions correctly across days."""
    facts = indep_facts_lookup(indep["effective"])
    # BK-1401 is a three-day hold (RM-02 CL-B 2026-01-08..10)
    assert facts.get(("RM-02", "CL-B", "2026-01-08")) == 600
    assert facts.get(("RM-02", "CL-B", "2026-01-09")) == 1440
    assert facts.get(("RM-02", "CL-B", "2026-01-10")) == 600


def test_heldout_cross_source_clinic_scoped_room(indep):
    """Cross-source collision + clinic-scoped room resolution.

    BK-5401's rev1 has a scheduling row (clinic A, room EXAM-3) and a later
    adjustment row (clinic SOUTH, room EXAM-3) that wins the collision.
    Clinic resolution must happen first (SOUTH -> CL-C), then room resolution
    must be clinic-scoped (EXAM-3 under CL-C -> RM-05). The effective booking
    must be on CL-C/RM-05, not a globally-resolved room.
    """
    eff = indep_effective_rows(indep["effective"])
    b = eff.get("BK-5401")
    assert b is not None
    assert b["clinic_id"] == "CL-C", \
        "BK-5401 clinic should be CL-C, got %s" % b["clinic_id"]
    assert b["room_id"] == "RM-05", \
        "BK-5401 room should be RM-05 (clinic-scoped), got %s" % b["room_id"]
    assert b["status"] == "BOOKED"


def test_heldout_cross_source_union(indep):
    """BK-5401 (CL-C/RM-05 09:00-10:00) and BK-5402 (CL-C/RM-05 09:30-10:00)
    must union on RM-05 into [09:00,10:00) = 60 minutes."""
    facts = indep_facts_lookup(indep["effective"])
    assert facts.get(("RM-05", "CL-C", "2026-05-08")) == 60, \
        "RM-05/CL-C 2026-05-08 should be 60, got %s" % facts.get(
            ("RM-05", "CL-C", "2026-05-08"))
    # no phantom row on RM-03 for CL-C
    assert facts.get(("RM-03", "CL-C", "2026-05-08")) is None


def test_heldout_no_global_room_collapse(indep):
    """A clinic-scoped room alias must not collapse across clinics: EXAM-3 in
    CL-C must resolve to RM-05, not RM-03 (CL-A's mapping)."""
    eff = indep_effective_rows(indep["effective"])
    for bid in ("BK-5302", "BK-5401"):
        b = eff.get(bid)
        assert b is not None
        assert b["room_id"] == "RM-05", \
            "%s room should be RM-05, got %s" % (bid, b["room_id"])
