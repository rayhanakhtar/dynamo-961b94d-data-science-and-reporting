"""Verifier for booking_state_history.csv (contract section 25).

Checks schema, uniqueness, sort, golden equality, and the authoritative
revision provenance semantics (effective_from/effective_to, is_effective).
"""

import csv
import os

from conftest import (HISTORY_PATH, EXPECTED_DIR, HISTORY_COLS,
                      indep_history, indep_effective_rows)


def _read(path):
    with open(path, encoding="utf-8", newline="") as h:
        return list(csv.DictReader(h))


def test_history_header(history_artifact):
    header, _rows = history_artifact
    assert header == HISTORY_COLS, "header mismatch: %r" % (header,)


def test_history_unique_key(history_artifact):
    _header, rows = history_artifact
    keys = [(r[0], r[1]) for r in rows]
    assert len(keys) == len(set(keys)), \
        "duplicate (booking_id, revision) in history"


def test_history_sorted(history_artifact):
    _header, rows = history_artifact
    keys = [(r[0], int(r[1])) for r in rows]
    assert keys == sorted(keys), "history not sorted booking_id/revision ASC"


def test_history_golden_match(history_artifact):
    header, rows = history_artifact
    with open(os.path.join(EXPECTED_DIR, "booking_state_history.csv"),
              encoding="utf-8", newline="") as h:
        exp = list(csv.reader(h))
    assert header == exp[0]
    assert rows == exp[1:], "history differs from golden"


def test_history_independent_semantics(history_artifact, indep):
    _header, rows = history_artifact
    produced = {}
    for r in rows:
        produced[(r[0], r[1])] = dict(zip(HISTORY_COLS, r))
    independent = indep_history(indep["effective"], indep["authoritative"],
                                indep["ledger"])
    assert set(produced) == set(independent), \
        "history key set mismatch: produced=%d independent=%d" % (
            len(produced), len(independent))
    for key in independent:
        assert produced[key] == independent[key], \
            "history row mismatch for %s" % (key,)


def test_history_one_effective_row(history_artifact, indep):
    _header, rows = history_artifact
    by_bid = {}
    for r in rows:
        by_bid.setdefault(r[0], []).append(r)
    for bid, rec in indep["effective"].items():
        bid_rows = by_bid.get(bid, [])
        eff_count = sum(1 for r in bid_rows if r[9] == "1")
        if rec is None:
            assert eff_count == 0, \
                "all-voided booking %s has is_effective=1" % bid
        else:
            assert eff_count == 1, \
                "booking %s has %d is_effective rows" % (bid, eff_count)


def test_history_effective_matches_effective_bookings(history_artifact,
                                                     effective_artifact):
    _hhist, hrows = history_artifact
    hist_eff = {}
    for r in hrows:
        if r[9] == "1":
            hist_eff[r[0]] = r
    _heff, erows = effective_artifact
    for e in erows:
        h = hist_eff.get(e[0])
        assert h is not None, "no history is_effective row for %s" % e[0]
        assert h[2] == e[1], "status mismatch %s" % e[0]
        assert h[3] == e[2], "clinic mismatch %s" % e[0]
        assert h[4] == e[3], "room mismatch %s" % e[0]
        assert h[5] == e[4], "start mismatch %s" % e[0]
        assert h[6] == e[5], "end mismatch %s" % e[0]
        assert int(h[1]) == int(e[8]), "revision mismatch %s" % e[0]


def test_history_effective_to_chain(history_artifact, indep):
    _header, rows = history_artifact
    by_bid = {}
    for r in rows:
        by_bid.setdefault(r[0], []).append(r)
    for bid, bid_rows in by_bid.items():
        bid_rows.sort(key=lambda r: int(r[1]))
        for i, r in enumerate(bid_rows):
            # effective_from = authoritative created_ts
            rec = indep["authoritative"].get((r[0], int(r[1])))
            assert rec is not None
            assert r[7] == rec["created_ts"], \
                "effective_from mismatch %r" % ((r[0], r[1]),)
            if i < len(bid_rows) - 1:
                nxt = bid_rows[i + 1]
                assert r[8] == nxt[7], \
                    "effective_to chain mismatch %s rev %s" % (r[0], r[1])
            else:
                assert r[8] == "", \
                    "highest revision effective_to not empty %s rev %s" % (r[0], r[1])
