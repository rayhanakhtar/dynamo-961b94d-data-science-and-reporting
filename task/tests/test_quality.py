"""Verifier for quality_summary.json (contract sections 17, 29).

Checks the categorized structure, integer/type invariants, exact golden
equality, and cross-artifact totals.
"""

import csv
import json
import os
import math

from conftest import (SUMMARY_PATH, EXPECTED_DIR, indep_facts_lookup,
                      indep_reconciliation)

QUALITY_CATEGORIES = ["event_quality", "identity_quality", "revision_quality",
                      "capacity_quality", "reconciliation_quality"]

EVENT_KEYS = ["total_raw_events", "duplicate_events_removed",
              "malformed_rows_rejected", "adjustment_rows_valid",
              "adjustment_rows_rejected"]
IDENTITY_KEYS = ["clinic_aliases_resolved", "room_aliases_resolved",
                 "identity_tokens_unresolved"]
REVISION_KEYS = ["total_booking_ids", "voided_revisions",
                 "bookings_with_effective_revision", "active_bookings",
                 "cancelled_bookings", "bookings_with_no_effective_revision",
                 "multi_day_active_bookings"]
CAPACITY_KEYS = ["total_blocked_minutes", "room_clinic_days_with_overlap",
                 "overlap_minutes_total"]
RECON_KEYS = ["reconciled_room_days", "reconciliation_mismatches"]


def _read_facts():
    with open(os.path.join(OUTPUT_DIR := "/app/output",
                           "daily_room_capacity_facts.csv"),
              encoding="utf-8", newline="") as h:
        return list(csv.reader(h))


def test_summary_parses_no_duplicates(summary_artifact):
    summary, duplicates = summary_artifact
    assert isinstance(summary, dict)
    assert not duplicates, "duplicate JSON keys: %r" % sorted(duplicates)


def test_summary_category_order(summary_artifact):
    summary, _duplicates = summary_artifact
    assert list(summary.keys()) == QUALITY_CATEGORIES, \
        "category order mismatch: %r" % (list(summary.keys()),)


def test_summary_inner_key_orders(summary_artifact):
    summary, _d = summary_artifact
    assert list(summary["event_quality"]) == EVENT_KEYS
    assert list(summary["identity_quality"]) == IDENTITY_KEYS
    assert list(summary["revision_quality"]) == REVISION_KEYS
    assert list(summary["capacity_quality"]) == CAPACITY_KEYS
    assert list(summary["reconciliation_quality"]) == RECON_KEYS


def test_summary_integer_values(summary_artifact):
    summary, _d = summary_artifact
    for cat in QUALITY_CATEGORIES:
        for key, value in summary[cat].items():
            assert isinstance(value, int) and not isinstance(value, bool), \
                "%s.%s not int: %r" % (cat, key, value)
            assert not math.isnan(value) and not math.isinf(value), \
                "%s.%s NaN/Inf" % (cat, key)


def test_summary_golden_match(summary_artifact):
    summary, _d = summary_artifact
    with open(os.path.join(EXPECTED_DIR, "quality_summary.json"),
              encoding="utf-8") as h:
        expected = json.load(h)
    assert summary == expected, "quality_summary differs from golden"


def test_summary_total_blocked_matches_facts():
    facts = _read_facts()
    total = sum(int(r[3]) for r in facts[1:])
    with open(SUMMARY_PATH, encoding="utf-8") as h:
        summary = json.load(h)
    assert summary["capacity_quality"]["total_blocked_minutes"] == total


def test_summary_totals_cross_artifact():
    with open(SUMMARY_PATH, encoding="utf-8") as h:
        summary = json.load(h)
    with open(os.path.join(EXPECTED_DIR, "capacity_reconciliation.csv"),
              encoding="utf-8", newline="") as h:
        rec = list(csv.DictReader(h))
    total_blocked = sum(int(r["blocked_minutes"]) for r in rec)
    total_overlap = sum(int(r["overlap_minutes"]) for r in rec)
    cq = summary["capacity_quality"]
    assert cq["total_blocked_minutes"] == total_blocked
    assert cq["overlap_minutes_total"] == total_overlap
    rq = summary["reconciliation_quality"]
    assert rq["reconciled_room_days"] == len(rec)
    assert rq["reconciliation_mismatches"] == 0


def test_summary_independent_populations(summary_artifact, indep):
    """Recompute metrics from their contract-designated populations."""
    summary, _d = summary_artifact
    records = indep["records"]
    effective = indep["effective"]
    # event_quality
    assert summary["event_quality"]["total_raw_events"] == indep["total_raw"]
    assert summary["event_quality"]["duplicate_events_removed"] == indep["duplicates"]
    assert summary["event_quality"]["malformed_rows_rejected"] == indep["malformed"]
    # identity_quality
    assert summary["identity_quality"] == indep["identity_stats"]
    # revision_quality
    eff_list = list(effective.values())
    assert summary["revision_quality"]["total_booking_ids"] == len(effective)
    assert summary["revision_quality"]["voided_revisions"] == \
        sum(1 for r in records if r["status"] == "VOIDED")
    assert summary["revision_quality"]["bookings_with_effective_revision"] == \
        sum(1 for r in eff_list if r is not None)
    assert summary["revision_quality"]["active_bookings"] == \
        sum(1 for r in eff_list if r is not None and r["status"] == "BOOKED")
    assert summary["revision_quality"]["cancelled_bookings"] == \
        sum(1 for r in eff_list if r is not None and r["status"] == "CANCELLED")
    assert summary["revision_quality"]["bookings_with_no_effective_revision"] == \
        sum(1 for r in eff_list if r is None)
