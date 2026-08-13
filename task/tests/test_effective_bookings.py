"""Verifier for effective_bookings.csv (contract section 24).

Checks schema, sort, uniqueness, exact golden equality, and independent
semantic recomputation of the effective booking snapshot.
"""

import csv
import os

from conftest import (EFFECTIVE_PATH, EXPECTED_DIR, EFFECTIVE_COLS,
                      indep_effective_rows)


def _read(path):
    with open(path, encoding="utf-8", newline="") as h:
        return list(csv.DictReader(h))


def test_effective_header(effective_artifact):
    header, _rows = effective_artifact
    assert header == EFFECTIVE_COLS, "header mismatch: %r" % (header,)


def test_effective_schema_types(effective_artifact):
    _header, rows = effective_artifact
    for row in rows:
        assert len(row) == len(EFFECTIVE_COLS), "bad column count %r" % row
        assert row[0], "empty booking_id"
        assert row[8].isdigit(), "effective_revision not integer: %r" % row


def test_effective_sorted(effective_artifact):
    _header, rows = effective_artifact
    assert [r[0] for r in rows] == sorted(r[0] for r in rows), \
        "effective_bookings not sorted by booking_id"


def test_effective_unique_booking(effective_artifact):
    _header, rows = effective_artifact
    bids = [r[0] for r in rows]
    assert len(bids) == len(set(bids)), "duplicate booking_id in effective_bookings"


def test_effective_golden_match(effective_artifact):
    header, rows = effective_artifact
    with open(os.path.join(EXPECTED_DIR, "effective_bookings.csv"),
              encoding="utf-8", newline="") as h:
        exp = list(csv.reader(h))
    assert header == exp[0]
    assert rows == exp[1:], "effective_bookings differs from golden"


def test_effective_independent_semantics(effective_artifact, indep):
    _header, rows = effective_artifact
    produced = {}
    for r in rows:
        produced[r[0]] = {
            "booking_id": r[0], "status": r[1], "clinic_id": r[2],
            "room_id": r[3], "start_ts": r[4], "end_ts": r[5],
            "provider_id": r[6], "source": r[7], "effective_revision": r[8],
        }
    independent = indep_effective_rows(indep["effective"])
    assert set(produced) == set(independent), \
        "booking_id set mismatch: produced=%d independent=%d" % (
            len(produced), len(independent))
    for bid in independent:
        assert produced[bid] == independent[bid], \
            "effective row mismatch for %s" % bid


def test_effective_all_voided_absent(effective_artifact, indep):
    _header, rows = effective_artifact
    produced = {r[0] for r in rows}
    # bookings whose every authoritative revision is VOIDED must be absent
    for bid, rec in indep["effective"].items():
        if rec is None:
            assert bid not in produced, \
                "all-voided booking %s present in effective_bookings" % bid


def test_effective_cross_source_collision(effective_artifact, indep):
    _header, rows = effective_artifact
    produced = {r[0]: r[8] for r in rows}
    for bid, rec in indep["effective"].items():
        if rec is None:
            continue
        assert produced.get(bid) == str(rec["revision"]), \
            "effective_revision mismatch for %s" % bid
