"""Verifier for capacity_reconciliation.csv (contract section 26).

Checks schema, conservation invariant, facts/reconciliation equality, the 1:1
room-day correspondence, and independent recomputation.
"""

import csv
import os

from conftest import (RECONCILIATION_PATH, EXPECTED_DIR,
                      RECONCILIATION_COLS, indep_reconciliation,
                      indep_facts_lookup)


def _read(path):
    with open(path, encoding="utf-8", newline="") as h:
        return list(csv.DictReader(h))


def test_reconciliation_header(reconciliation_artifact):
    header, _rows = reconciliation_artifact
    assert header == RECONCILIATION_COLS, "header mismatch: %r" % (header,)


def test_reconciliation_sorted(reconciliation_artifact):
    _header, rows = reconciliation_artifact
    keys = [(r[0], r[1], r[2]) for r in rows]
    assert keys == sorted(keys), "reconciliation not sorted room/clinic/date ASC"


def test_reconciliation_unique(reconciliation_artifact):
    _header, rows = reconciliation_artifact
    keys = [tuple(r[:3]) for r in rows]
    assert len(set(keys)) == len(keys), "duplicate reconciliation room-day"


def test_reconciliation_golden_match(reconciliation_artifact):
    header, rows = reconciliation_artifact
    with open(os.path.join(EXPECTED_DIR, "capacity_reconciliation.csv"),
              encoding="utf-8", newline="") as h:
        exp = list(csv.reader(h))
    assert header == exp[0]
    assert rows == exp[1:], "reconciliation differs from golden"


def test_conservation_invariant(reconciliation_artifact):
    _header, rows = reconciliation_artifact
    for r in rows:
        raw = int(r[4])
        blocked = int(r[5])
        overlap = int(r[6])
        assert raw == blocked + overlap, \
            "raw != blocked + overlap for %r" % (tuple(r[:3]),)
        assert raw >= 0 and blocked >= 0 and overlap >= 0, \
            "negative value for %r" % (tuple(r[:3]),)


def test_reconciliation_blocked_equals_facts(reconciliation_artifact,
                                             facts_artifact):
    _hr, rrows = reconciliation_artifact
    _hf, frows = facts_artifact
    fact_map = {(r[0], r[1], r[2]): int(r[3]) for r in frows}
    for r in rrows:
        key = (r[0], r[1], r[2])
        assert fact_map.get(key) == int(r[5]), \
            "reconciliation.blocked != facts.blocked for %r" % (key,)


def test_reconciliation_facts_bijection(reconciliation_artifact,
                                        facts_artifact):
    _hr, rrows = reconciliation_artifact
    _hf, frows = facts_artifact
    rkeys = {tuple(r[:3]) for r in rrows}
    fkeys = {tuple(r[:3]) for r in frows}
    assert rkeys == fkeys, \
        "reconciliation/facts room-day sets differ: %d vs %d" % (
            len(rkeys), len(fkeys))


def test_reconciliation_independent_semantics(reconciliation_artifact, indep):
    _header, rows = reconciliation_artifact
    produced = {}
    for r in rows:
        produced[tuple(r[:3])] = {
            "effective_booking_count": int(r[3]),
            "raw_interval_minutes": int(r[4]),
            "blocked_minutes": int(r[5]),
            "overlap_minutes": int(r[6]),
        }
    independent = indep_reconciliation(indep["effective"])
    assert set(produced) == set(independent), \
        "reconciliation key set mismatch"
    for key in independent:
        assert produced[key] == independent[key], \
            "reconciliation mismatch for %r" % (key,)
