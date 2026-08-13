"""Verifier for daily_room_capacity_facts.csv (contract sections 14-15).

Checks schema, uniqueness, sort, positive minutes, exact golden equality, and
independent recomputation of the shared room/clinic union from effective state.
"""

import csv
import os

from conftest import (FACTS_PATH, EXPECTED_DIR, FACTS_COLS,
                      indep_facts_lookup, indep_union_minutes,
                      indep_day_segments)


def _read(path):
    with open(path, encoding="utf-8", newline="") as h:
        return list(csv.DictReader(h))


def test_facts_header(facts_artifact):
    header, _rows = facts_artifact
    assert header == FACTS_COLS, "header mismatch: %r" % (header,)


def test_facts_columns_and_positive(facts_artifact):
    _header, rows = facts_artifact
    assert rows, "fact table is empty"
    for row in rows:
        assert len(row) == 4, "row has %d columns: %r" % (len(row), row)
        try:
            minutes = int(row[3])
        except ValueError:
            raise AssertionError("blocked_minutes not int: %r" % row)
        assert str(minutes) == row[3], "blocked_minutes not plain int: %r" % row
        assert minutes >= 1, "blocked_minutes must be positive: %r" % row


def test_facts_unique(facts_artifact):
    _header, rows = facts_artifact
    keys = [tuple(row[:3]) for row in rows]
    assert len(set(keys)) == len(keys), "duplicate (room, clinic, date) rows"


def test_facts_sorted(facts_artifact):
    _header, rows = facts_artifact
    expected = sorted(rows, key=lambda r: (r[0], r[1], r[2]))
    assert rows == expected, "facts not sorted room/clinic/date ASC"


def test_facts_golden_match(facts_artifact):
    header, rows = facts_artifact
    with open(os.path.join(EXPECTED_DIR, "daily_room_capacity_facts.csv"),
              encoding="utf-8", newline="") as h:
        exp = list(csv.reader(h))
    assert header == exp[0]
    assert rows == exp[1:], "facts differ from golden"


def test_facts_independent_semantics(facts_artifact, indep):
    _header, rows = facts_artifact
    produced = {}
    for r in rows:
        produced[(r[0], r[1], r[2])] = int(r[3])
    independent = indep_facts_lookup(indep["effective"])
    assert set(produced) == set(independent), \
        "fact key set mismatch: produced=%d independent=%d" % (
            len(produced), len(independent))
    for key in independent:
        assert produced[key] == independent[key], \
            "fact mismatch for %r" % (key,)


def test_facts_no_superseded_leakage(facts_artifact, indep):
    """Every fact blocked minute must be explainable by an effective BOOKED
    booking (superseded/cancelled/voided content never contributes)."""
    _header, rows = facts_artifact
    produced = {(r[0], r[1], r[2]): int(r[3]) for r in rows}
    independent = indep_facts_lookup(indep["effective"])
    for key in produced:
        assert key in independent, \
            "fact row %r not derivable from effective state" % (key,)


def test_facts_provider_source_non_partition(facts_artifact, indep):
    """Two effective BOOKED bookings on the same room/day must union on one
    timeline regardless of provider/source."""
    # This is enforced by indep_facts_lookup using shared-room union; the
    # independent recomputation equality test already verifies it. Add a direct
    # check that any room-day with two contributing bookings is not summed.
    _header, rows = facts_artifact
    lookup = {(r[0], r[1], r[2]): int(r[3]) for r in rows}
    contrib = {}
    for rec in indep["effective"].values():
        if rec is None or rec["status"] != "BOOKED":
            continue
        for day, lo, hi in indep_day_segments(rec["start_ts"], rec["end_ts"]):
            key = (rec["room_id"], rec["clinic_id"], day.isoformat())
            contrib.setdefault(key, []).append((lo, hi))
    for key, iv in contrib.items():
        union = indep_union_minutes(iv)
        assert lookup.get(key) == union, \
            "room-day %r must be union (%d), not sum" % (key, union)
