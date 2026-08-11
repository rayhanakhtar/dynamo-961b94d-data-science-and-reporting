"""
Verifier for the "Daily Room Capacity Facts" task.

Validates the agent's already-produced artifacts:

    /app/output/daily_room_capacity_facts.csv
    /app/output/quality_summary.json

against the golden expected outputs under /tests/expected/ (overlaid by Harbor
at verify time) and against the business rules in
/app/data/docs/fact_contract.md.

The verifier NEVER imports, executes, or re-runs the agent's prepare_facts.py
(or any agent code) and never reads hidden fixtures during verification. It
only inspects the two output files the agent was asked to produce. The golden
outputs live in /tests/expected/ (outside environment/, so the agent cannot
see them during its run).

Reward is all-or-nothing: every test must pass for reward 1.0.
"""

import csv
import json
import os

import pytest

OUTPUT_DIR = "/app/output"
FACTS_PATH = os.path.join(OUTPUT_DIR, "daily_room_capacity_facts.csv")
SUMMARY_PATH = os.path.join(OUTPUT_DIR, "quality_summary.json")

# /tests/test_outputs.py  ->  /tests/expected/
EXPECTED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "expected")
EXPECTED_FACTS = os.path.join(EXPECTED_DIR,
                              "daily_room_capacity_facts.csv")
EXPECTED_SUMMARY = os.path.join(EXPECTED_DIR, "quality_summary.json")

FACT_COLUMNS = ["room_id", "clinic_id", "calendar_date", "blocked_minutes"]

# Exact contract key set, in the contract order (fact_contract.md section 17).
SUMMARY_KEYS = [
    "total_raw_events",
    "duplicate_events_removed",
    "malformed_rows_rejected",
    "total_booking_ids",
    "voided_revisions",
    "bookings_with_effective_revision",
    "active_bookings",
    "cancelled_bookings",
    "bookings_with_no_effective_revision",
    "multi_day_active_bookings",
    "room_clinic_days_with_overlap",
    "total_blocked_minutes",
]

# Independently audited whole-dataset total (fact_contract.md sections 15-17).
TOTAL_BLOCKED_MINUTES = 51093


# ---------------------------------------------------------------------------
# Artifact readers
# ---------------------------------------------------------------------------

def read_facts(path):
    """Return (header, rows) where rows are raw lists of 4 strings."""
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        rows = list(reader)
    return header, rows


def read_facts_lookup(rows):
    """Map (room_id, clinic_id, calendar_date) -> blocked_minutes."""
    lookup = {}
    for row in rows:
        room, clinic, date, minutes = row
        key = (room, clinic, date)
        if key in lookup:
            raise AssertionError("duplicate fact row for %r" % (key,))
        lookup[key] = int(minutes)
    return lookup


def read_summary(path):
    """Parse the quality summary JSON, detecting duplicate object keys."""
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
def facts_rows():
    header, rows = read_facts(FACTS_PATH)
    return header, rows


@pytest.fixture(scope="module")
def facts_lookup(facts_rows):
    _header, rows = facts_rows
    return read_facts_lookup(rows)


@pytest.fixture(scope="module")
def summary_data():
    return read_summary(SUMMARY_PATH)


# ---------------------------------------------------------------------------
# 1. Files exist, are regular files, and live inside /app/output
# ---------------------------------------------------------------------------

def test_output_files_exist():
    assert os.path.exists(FACTS_PATH), "missing %s" % FACTS_PATH
    assert os.path.exists(SUMMARY_PATH), "missing %s" % SUMMARY_PATH


def test_outputs_are_regular_files_within_output_dir():
    for path in (FACTS_PATH, SUMMARY_PATH):
        assert not os.path.islink(path), \
            "%s is a symlink" % path
        assert os.path.isfile(path), "%s is not a regular file" % path
        assert os.path.realpath(path).startswith(
            os.path.realpath(OUTPUT_DIR) + os.sep), \
            "%s resolves outside %s" % (path, OUTPUT_DIR)


# ---------------------------------------------------------------------------
# 2. CSV schema, shape, ordering, uniqueness
# ---------------------------------------------------------------------------

def test_facts_header():
    header, _rows = read_facts(FACTS_PATH)
    assert header == FACT_COLUMNS, "header mismatch: %r" % (header,)


def test_facts_columns_and_minutes(facts_rows):
    _header, rows = facts_rows
    assert rows, "fact table is empty"
    for row in rows:
        assert len(row) == 4, "row has %d columns: %r" % (len(row), row)
        try:
            minutes = int(row[3])
        except ValueError:
            raise AssertionError("blocked_minutes not an integer: %r" % row)
        assert str(minutes) == row[3], \
            "blocked_minutes not a plain integer: %r" % row
        assert minutes >= 1, "blocked_minutes must be positive: %r" % row


def test_facts_row_count(facts_rows):
    _header, rows = facts_rows
    assert len(rows) == 235, "expected 235 fact rows, got %d" % len(rows)


def test_facts_unique_cells(facts_rows):
    _header, rows = facts_rows
    keys = [tuple(row[:3]) for row in rows]
    assert len(set(keys)) == len(keys), "duplicate (room, clinic, date) rows"


def test_facts_sorted(facts_rows):
    _header, rows = facts_rows
    expected = sorted(rows, key=lambda r: (r[0], r[1], r[2]))
    assert rows == expected, "fact rows are not sorted room/clinic/date ASC"


# ---------------------------------------------------------------------------
# 3. Exact value comparison against the golden expected outputs
# ---------------------------------------------------------------------------

def test_facts_exact_match():
    header, rows = read_facts(FACTS_PATH)
    exp_header, exp_rows = read_facts(EXPECTED_FACTS)
    assert header == exp_header
    assert rows == exp_rows, \
        "fact rows differ from the expected table (%d vs %d rows)" % (
            len(rows), len(exp_rows))


def test_facts_total_blocked_minutes(facts_lookup):
    total = sum(facts_lookup.values())
    assert total == TOTAL_BLOCKED_MINUTES, \
        "sum(blocked_minutes) = %d, expected %d" % (total,
                                                    TOTAL_BLOCKED_MINUTES)


def test_summary_parses_and_no_duplicate_keys():
    summary, duplicates = read_summary(SUMMARY_PATH)
    assert isinstance(summary, dict), "quality summary is not an object"
    assert not duplicates, "duplicate JSON keys: %r" % sorted(duplicates)


def test_summary_exact_key_set_and_order():
    summary, _duplicates = read_summary(SUMMARY_PATH)
    assert list(summary.keys()) == SUMMARY_KEYS, \
        "key set/order mismatch: %r" % (list(summary.keys()),)


def test_summary_integer_values():
    summary, _duplicates = read_summary(SUMMARY_PATH)
    for key, value in summary.items():
        assert isinstance(value, int) and not isinstance(value, bool), \
            "%s is not an integer: %r" % (key, value)


def test_summary_values_match_golden():
    summary, _duplicates = read_summary(SUMMARY_PATH)
    with open(EXPECTED_SUMMARY, encoding="utf-8") as handle:
        expected = json.load(handle)
    for key in SUMMARY_KEYS:
        assert summary.get(key) == expected[key], \
            "%s = %r, expected %r" % (key, summary.get(key), expected[key])


def test_summary_total_matches_csv_sum(facts_lookup):
    summary, _duplicates = read_summary(SUMMARY_PATH)
    assert summary["total_blocked_minutes"] == sum(facts_lookup.values()), \
        "JSON total_blocked_minutes does not equal the CSV blocked-minute sum"


def test_summary_consistency_identities():
    summary, _duplicates = read_summary(SUMMARY_PATH)
    assert summary["total_booking_ids"] == \
        summary["bookings_with_effective_revision"] + \
        summary["bookings_with_no_effective_revision"], \
        "identity 1 violated"
    assert summary["active_bookings"] + summary["cancelled_bookings"] == \
        summary["bookings_with_effective_revision"], \
        "identity 2 violated"


# ---------------------------------------------------------------------------
# 4. Atomic semantic checks — revision handling (fact_contract.md sections 5-8)
# ---------------------------------------------------------------------------

REVISION_CELLS = {
    # BK-1501: booked then the highest revision is VOIDED; the booking must
    # still be effective BOOKED via rev 1 [09:00, 10:00).
    "BK-1501 voided max skipped": (("RM-01", "CL-A", "2026-01-11"), 60),
    # BK-1502: amendment kept; the highest revision (rev 3) is VOIDED, so the
    # effective rev 2 [10:00, 13:00) must stand.
    "BK-1502 amendment kept": (("RM-02", "CL-B", "2026-01-11"), 180),
    # BK-1503: booked -> cancelled -> voided; the void is skipped and the
    # cancellation remains effective, so no capacity.
    "BK-1503 cancel stands": (("RM-02", "CL-A", "2026-01-29"), None),
    # BK-1504: every revision VOIDED -> no effective revision, no capacity.
    "BK-1504 all voided": (("RM-02", "CL-C", "2026-01-29"), None),
    # BK-1505: rev 1 VOIDED, rev 2 booked -> effective [14:00, 15:00).
    "BK-1505 voided rev1 skipped": (("RM-01", "CL-C", "2026-01-29"), 60),
    # BK-1506: booked -> voided -> cancelled -> effective cancellation.
    "BK-1506 cancelled": (("RM-03", "CL-A", "2026-01-11"), None),
    # BK-1507: booked -> cancelled -> no capacity.
    "BK-1507 cancelled": (("RM-04", "CL-A", "2026-01-11"), None),
    # BK-1508: amended then cancelled -> no capacity.
    "BK-1508 cancelled after amendment": (("RM-03", "CL-A", "2026-01-29"), None),
    # BK-1509: cancelled then re-booked with a new interval [14:00, 16:00).
    "BK-1509 re-booked": (("RM-01", "CL-B", "2026-01-11"), 120),
    # BK-1600: same (booking, revision) twice; the row with the latest
    # created_ts wins -> effective [10:30, 11:30).
    "BK-1600 created_ts collision": (("RM-02", "CL-A", "2026-01-13"), 60),
    # P8: an overlap where one participant (BK-0802) is cancelled -> only the
    # remaining active booking [09:00, 12:00) counts, no union is needed.
    "P8 cancelled participant": (("RM-01", "CL-B", "2026-01-19"), 180),
}


@pytest.mark.parametrize("label", sorted(REVISION_CELLS))
def test_revision_cells(facts_lookup, label):
    (room, clinic, date), expected = REVISION_CELLS[label]
    key = (room, clinic, date)
    actual = facts_lookup.get(key)
    assert actual == expected, \
        "%s: %r = %r, expected %r" % (label, key, actual, expected)


# ---------------------------------------------------------------------------
# 5. Atomic semantic checks — overlap/union (fact_contract.md sections 11-13)
# ---------------------------------------------------------------------------

# 24 (room, clinic, day) cells whose blocked minutes are a UNION of overlapping
# active bookings from different providers/sources on one shared room timeline.
OVERLAP_CELLS = {
    # P1 chain: [09,12)+[10,11)+[11,14) etc. across different providers.
    "P1 chain CL-A/RM-01": (("RM-01", "CL-A", "2026-01-06"), 300),
    "P1 chain CL-B/RM-01": (("RM-01", "CL-B", "2026-01-13"), 240),
    "P1 chain CL-A/RM-03": (("RM-03", "CL-A", "2026-01-20"), 240),
    "P1 chain CL-C/RM-01": (("RM-01", "CL-C", "2026-02-03"), 300),
    # P2 nested: a full-day booking [08,17) containing shorter ones.
    "P2 nested CL-A/RM-02": (("RM-02", "CL-A", "2026-01-07"), 540),
    "P2 nested CL-B/RM-02": (("RM-02", "CL-B", "2026-01-21"), 480),
    "P2 nested CL-A/RM-04": (("RM-04", "CL-A", "2026-02-04"), 300),
    "P2 nested CL-C/RM-01": (("RM-01", "CL-C", "2026-02-10"), 180),
    # P3 identical: bookings on the exact same slot count once.
    "P3 identical CL-A/RM-01": (("RM-01", "CL-A", "2026-01-08"), 60),
    "P3 identical CL-B/RM-01": (("RM-01", "CL-B", "2026-02-05"), 60),
    "P3 identical CL-A/RM-02": (("RM-02", "CL-A", "2026-02-12"), 30),
    # P4 partial: [09,11)+[10,12) -> 180, etc.
    "P4 partial CL-A/RM-03": (("RM-03", "CL-A", "2026-01-09"), 180),
    "P4 partial CL-B/RM-01": (("RM-01", "CL-B", "2026-01-15"), 150),
    "P4 partial CL-A/RM-01": (("RM-01", "CL-A", "2026-01-27"), 150),
    "P4 partial CL-C/RM-01": (("RM-01", "CL-C", "2026-02-06"), 150),
    # P5 long chains: five and six overlapping segments.
    "P5 five-segment chain": (("RM-02", "CL-B", "2026-01-12"), 330),
    "P5 six-segment chain": (("RM-03", "CL-A", "2026-02-02"), 420),
    # P6 two overlapping clusters separated by a gap.
    "P6 two clusters": (("RM-02", "CL-A", "2026-01-14"), 210),
    # P7 overlap across midnight: next-day continuation overlaps a booking.
    "P7 overnight overlap": (("RM-04", "CL-A", "2026-01-17"), 60),
    # P9 an amended participant's moved interval still unions to 90.
    "P9 amended participant": (("RM-01", "CL-A", "2026-01-22"), 90),
    # P10 same room_id in two clinics on the same date = separate grains.
    "P10 clinic CL-A": (("RM-01", "CL-A", "2026-01-26"), 180),
    "P10 clinic CL-B": (("RM-01", "CL-B", "2026-01-26"), 180),
    # P11 an overlap plus a separate non-overlapping booking.
    "P11 overlap plus normal": (("RM-02", "CL-A", "2026-01-28"), 180),
    # P12 bookend chain [09,11)+[10,12)+[12,14) -> 300, touching at the join.
    "P12 bookend chain": (("RM-04", "CL-A", "2026-01-23"), 300),
}


@pytest.mark.parametrize("label", sorted(OVERLAP_CELLS))
def test_overlap_cells(facts_lookup, label):
    (room, clinic, date), expected = OVERLAP_CELLS[label]
    key = (room, clinic, date)
    actual = facts_lookup.get(key)
    assert actual == expected, \
        "%s: %r = %r, expected %r" % (label, key, actual, expected)


# ---------------------------------------------------------------------------
# 6. Atomic semantic checks — boundaries (fact_contract.md sections 9-10)
# ---------------------------------------------------------------------------

BOUNDARY_CELLS = {
    # BK-1401: three-day hold, 600 / 1440 / 600.
    "BK-1401 day 1": (("RM-02", "CL-B", "2026-01-08"), 600),
    "BK-1401 day 2": (("RM-02", "CL-B", "2026-01-09"), 1440),
    "BK-1401 day 3": (("RM-02", "CL-B", "2026-01-10"), 600),
    # BK-1402: overnight 23:30 -> 00:30, 30 minutes on each day.
    "BK-1402 day 1": (("RM-01", "CL-A", "2026-01-20"), 30),
    "BK-1402 day 2": (("RM-01", "CL-A", "2026-01-21"), 30),
    # BK-1403: overnight 23:30 -> 00:30, 30 minutes on each day.
    "BK-1403 day 1": (("RM-01", "CL-B", "2026-02-06"), 30),
    "BK-1403 day 2": (("RM-01", "CL-B", "2026-02-07"), 30),
    # BK-1404: ends exactly at midnight, 120 on day 1 and 0 on day 2 (the
    # 180 on 2026-02-10 comes only from P2d, proving day 2 is empty).
    "BK-1404 day 1": (("RM-01", "CL-C", "2026-02-09"), 120),
    "BK-1404 day 2 empty": (("RM-01", "CL-C", "2026-02-10"), 180),
    # BK-1405: starts exactly at midnight, full day then 480.
    "BK-1405 day 1": (("RM-03", "CL-A", "2026-02-11"), 1440),
    "BK-1405 day 2": (("RM-03", "CL-A", "2026-02-12"), 480),
    # BK-1406: 23:59 -> 00:01, one minute on each day.
    "BK-1406 day 1": (("RM-01", "CL-A", "2026-01-30"), 1),
    "BK-1406 day 2": (("RM-01", "CL-A", "2026-01-31"), 1),
    # BK-1301/BK-1302: zero-length intervals block nothing.
    "BK-1301 zero-length": (("RM-01", "CL-C", "2026-01-15"), None),
    "BK-1302 zero-length": (("RM-02", "CL-B", "2026-02-05"), None),
    # BK-1204/BK-1205: touching intervals [09,10)+[10,11) are NOT an overlap.
    "BK-1204/1205 touching": (("RM-02", "CL-A", "2026-02-09"), 120),
    # BK-1206/BK-1207: two separate blocks with a gap in between.
    "BK-1206/1207 gap": (("RM-01", "CL-A", "2026-02-11"), 120),
}


@pytest.mark.parametrize("label", sorted(BOUNDARY_CELLS))
def test_boundary_cells(facts_lookup, label):
    (room, clinic, date), expected = BOUNDARY_CELLS[label]
    key = (room, clinic, date)
    actual = facts_lookup.get(key)
    assert actual == expected, \
        "%s: %r = %r, expected %r" % (label, key, actual, expected)


# ---------------------------------------------------------------------------
# 7. Atomic semantic checks — collision x voided composition
#    (fact_contract.md sections 5-8, 12)
# ---------------------------------------------------------------------------
#
# These cells exercise the composition of the same-revision collision rule
# with the VOIDED-skip effective-revision scan and the shared-room union:
# collision resolution (section 5) runs FIRST and fixes each revision's sole
# content; only then does the descending-revision scan skip VOIDED winners and
# pick the effective revision; that effective interval then feeds the union.

COLLISION_CELLS = {
    # BK-1701 + BK-1702 (different providers/sources) share RM-01 on
    # 2026-01-18. BK-1701's rev2 collision winner is the later-created BOOKED
    # row [11:30, 13:30); the earlier VOIDED copy must not win. The effective
    # intervals [11:30, 13:30) and [10:00, 12:00) union to [10:00, 13:30) =
    # 210, never the 240 sum of the two bookings.
    "collision winner feeds shared-room union": (("RM-01", "CL-A", "2026-01-18"), 210),
    # BK-1703 control: two rev2 rows, the first physical copy CANCELLED with an
    # earlier created_ts, the second BOOKED with a later created_ts. The
    # later-created BOOKED row must win regardless of file order, so the
    # booking stays active [09:00, 10:00).
    "collision control later-created booked wins": (("RM-03", "CL-A", "2026-01-18"), 60),
    # BK-1704: the rev2 collision winner is the latest-created row, which is
    # VOIDED (its physical order opposes its created_ts order). The
    # effective-revision scan skips the voided winner and falls back to
    # rev1 [09:00, 11:00).
    "collided void skipped falls back to rev1": (("RM-04", "CL-A", "2026-01-18"), 120),
    # BK-1705 control: two rev2 rows tie on created_ts; the greatest event_id
    # (the BOOKED row) wins, keeping the booking active [11:00, 12:00).
    "collision tie-break greatest event_id": (("RM-02", "CL-A", "2026-01-18"), 60),
}


@pytest.mark.parametrize("label", sorted(COLLISION_CELLS))
def test_collision_cells(facts_lookup, label):
    (room, clinic, date), expected = COLLISION_CELLS[label]
    key = (room, clinic, date)
    actual = facts_lookup.get(key)
    assert actual == expected, \
        "%s: %r = %r, expected %r" % (label, key, actual, expected)


# ---------------------------------------------------------------------------
# 8. Atomic semantic checks — deduplication x malformed-row precedence
#    (fact_contract.md sections 4, 16, 8, 14)
# ---------------------------------------------------------------------------
#
# These cells pin the ordering of input-cleanup rules over state resolution.
# Section 4 keeps only the FIRST copy of each event_id in file order; every
# later copy is a discarded duplicate and never reaches validation. Malformed
# detection (section 16) is applied to the rows that survive deduplication, so
# the surviving copy of a duplicated event is the one validated. The two
# bookings below carry an event_id exported twice with conflicting copies:

DEDUP_PRECEDENCE_CELLS = {
    # BK-1801: the first copy of EV-0000914 is malformed (end 08:00 before
    # start 09:00); the later copy is well-formed but is a duplicate and is
    # discarded. The surviving (first) copy is malformed, so the booking has
    # no well-formed row and contributes NOTHING: no fact row for this cell.
    "poisoned copy dead booking": (("RM-01", "CL-B", "2026-01-18"), None),
    # BK-1802: the first copy of EV-0000915 is well-formed [09:00, 10:00); the
    # later copy is malformed but is a duplicate and is discarded before
    # validation. The booking stays active: 60 blocked minutes.
    "malformed duplicate ignored": (("RM-02", "CL-B", "2026-01-18"), 60),
}


@pytest.mark.parametrize("label", sorted(DEDUP_PRECEDENCE_CELLS))
def test_dedup_precedence_cells(facts_lookup, label):
    (room, clinic, date), expected = DEDUP_PRECEDENCE_CELLS[label]
    key = (room, clinic, date)
    actual = facts_lookup.get(key)
    assert actual == expected, \
        "%s: %r = %r, expected %r" % (label, key, actual, expected)


# ---------------------------------------------------------------------------
# 9. Atomic semantic checks — cascading composed scenarios
#    (fact_contract.md sections 4-12)
# ---------------------------------------------------------------------------
#
# Each cell pins the DOWNSTREAM consequence of an UPSTREAM decision. These
# scenarios make pipeline stages order-dependent: a wrong choice at one stage
# changes which room, which days, or which union value reach the fact table.

COMPOSED_CELLS = {
    # S1 room reassignment (sections 5, 7, 11, 12): BK-2101 was amended from
    # RM-01 to RM-02. Its rev2 has two rows: a VOIDED RM-01 row (earlier
    # created_ts) and a BOOKED RM-02 row (later created_ts). The later-created
    # BOOKED row wins the collision, so the effective revision lives on RM-02;
    # the union with BK-2104 (different provider) on RM-02 is
    # [10:00,11:30) = 90. The superseded RM-01 revision must emit nothing.
    "room reassignment lands on new room": (("RM-02", "CL-A", "2026-02-26"), 90),
    "superseded room emits nothing": (("RM-01", "CL-A", "2026-02-26"), None),
    # S2 midnight amendment (sections 5, 7, 9, 10, 12): BK-2102's rev2
    # collision winner is a VOIDED row (later created_ts), which is skipped;
    # the effective rev1 interval [23:00, 01:00) crosses midnight, so it
    # contributes 60 minutes on each day. On 2026-02-21 it overlaps
    # BK-2103 [00:30, 01:30) (different provider), whose union is
    # [00:00, 01:30) = 90.
    "midnight amendment day 1": (("RM-01", "CL-B", "2026-02-20"), 60),
    "midnight amendment day 2 union": (("RM-01", "CL-B", "2026-02-21"), 90),
    # S3 re-exported revocation (sections 4, 5, 7, 16, 12): BK-2105's rev2
    # VOIDED event was exported twice; the first copy is malformed, so the
    # event is discarded before collision resolution (the clean second copy is
    # a discarded duplicate). rev2 therefore resolves to the BOOKED row
    # [10:00, 11:00); the union with BK-2106 [10:30, 11:30) is
    # [10:00, 11:30) = 90. A keep-last/validate-before-dedup pipeline would
    # let the VOIDED row survive and fall back to rev1 [09:00, 10:00), giving
    # a 120-minute gap-union instead.
    "re-exported revocation union": (("RM-01", "CL-C", "2026-02-27"), 90),
}


@pytest.mark.parametrize("label", sorted(COMPOSED_CELLS))
def test_composed_cells(facts_lookup, label):
    (room, clinic, date), expected = COMPOSED_CELLS[label]
    key = (room, clinic, date)
    actual = facts_lookup.get(key)
    assert actual == expected, \
        "%s: %r = %r, expected %r" % (label, key, actual, expected)


# ---------------------------------------------------------------------------
# 10. Atomic semantic checks — state-dependent cascading composition
#     (fact_contract.md sections 4-17)
# ---------------------------------------------------------------------------
#
# Each cell fixes the outcome of a decision chain: deduplication or collision
# selection changes which revision content survives, which room it lands on,
# which days it spans, and how it merges with another provider. A wrong choice
# at any earlier stage produces a different fact value here.

STATE_DEPENDENT_CELLS = {
    # Family A, tie-break reassignment (sections 5, 7, 11, 12): BK-2201's rev2
    # has a VOIDED RM-01 row and a BOOKED RM-02 row with the SAME created_ts;
    # section 5 breaks the tie by greatest event_id, so the BOOKED RM-02 row
    # wins, rev3 is VOIDED and skipped, and the effective [14:00,15:30)
    # interval on RM-02 merges with BK-2202 [14:30,15:00) into 90 minutes.
    # The superseded RM-01 revision emits nothing.
    "tie-break reassignment lands on RM-02": (("RM-02", "CL-A", "2026-03-05"), 90),
    "tie-break reassignment old room absent": (("RM-01", "CL-A", "2026-03-05"), None),
    # Family B, revoked reschedule (sections 5, 7, 9, 10, 12): BK-2203's rev2
    # collision winner is VOIDED (created later) and rev3 is VOIDED, so the
    # effective rev1 [23:00,00:30) crosses midnight: 60 minutes on 2026-03-06
    # and [00:00,00:30) on 2026-03-07, which merges with BK-2204 [00:15,00:45)
    # into 45 minutes on the second day.
    "revoked reschedule day 1": (("RM-02", "CL-B", "2026-03-06"), 60),
    "revoked reschedule day 2 union": (("RM-02", "CL-B", "2026-03-07"), 45),
    # Family C, inverse poison (sections 4, 5, 7, 16, 12): the first-copy
    # deduplication decides whether the rev2 VOIDED event survives at all.
    # BK-2205's duplicate is valid-first, so its VOIDED row survives, wins the
    # collision, is skipped, and the booking falls back to rev1 [09:00,10:00)
    # (60 with BK-2207 [09:15,09:45)). BK-2206's duplicate is malformed-first,
    # so its VOIDED event is discarded before the collision and rev2's BOOKED
    # [10:00,11:00) row is effective (90 with BK-2208 [10:30,11:30)).
    "valid-first duplicate falls back to rev1": (("RM-03", "CL-A", "2026-03-07"), 60),
    "malformed-first duplicate keeps rev2": (("RM-01", "CL-C", "2026-03-07"), 90),
    # Family D, superseded room with colliding neighbor (sections 5, 7, 11,
    # 12): BK-2209 moved from RM-04 to RM-03 (rev2 collision: VOIDED RM-04 row
    # loses to the BOOKED RM-03 row by created_ts). The superseded RM-04
    # interval must not leak: RM-04 carries only BK-2210 [09:30,10:30) = 60,
    # while the effective [10:00,12:00) on RM-03 merges with BK-2211
    # [11:00,12:30) into 150.
    "superseded room carries only neighbor": (("RM-04", "CL-A", "2026-03-08"), 60),
    "destination room union": (("RM-03", "CL-A", "2026-03-08"), 150),
    # Full-chain booking (sections 4, 5, 7, 8, 9, 10, 12): BK-2212's rev2
    # VOIDED event survives as its valid first copy and wins the collision
    # (created later); rev3 is VOIDED, so the effective rev1 [23:00,00:30)
    # crosses midnight on RM-01: 60 minutes on 2026-03-09 and [00:00,00:30) on
    # 2026-03-10, which merges with BK-2213 [00:15,00:45) into 45.
    "full chain midnight day 1": (("RM-01", "CL-A", "2026-03-09"), 60),
    "full chain midnight day 2 union": (("RM-01", "CL-A", "2026-03-10"), 45),
}


@pytest.mark.parametrize("label", sorted(STATE_DEPENDENT_CELLS))
def test_state_dependent_cells(facts_lookup, label):
    (room, clinic, date), expected = STATE_DEPENDENT_CELLS[label]
    key = (room, clinic, date)
    actual = facts_lookup.get(key)
    assert actual == expected, \
        "%s: %r = %r, expected %r" % (label, key, actual, expected)


# ---------------------------------------------------------------------------
# 11. Atomic semantic checks — cross-stage calibration fixtures
#     (fact_contract.md sections 3-17)
# ---------------------------------------------------------------------------
#
# Each cell fixes the downstream consequence of a precise semantic rule:
# the same-created_ts collision tie-break, the half-open [start, end)
# boundary (including intervals that end exactly at midnight), the union of
# overlapping intervals, the dedup/validation interaction with collision
# resolution, the treatment of touching intervals for the overlap summary,
# and the minute-truncation of seconds-bearing times.

CROSS_STAGE_CELLS = {
    # Collision tie-break (sections 5, 7, 12): BK-2301's rev2 has two rows
    # with the SAME created_ts; section 5 breaks the tie by greatest event_id,
    # so the BOOKED RM-02 row wins and the effective [13:00,14:30) merges with
    # BK-2302 [13:15,13:45) into 90 minutes on RM-02. The superseded RM-01
    # revision emits nothing.
    "tie-break collision lands on RM-02": (("RM-02", "CL-A", "2026-03-12"), 90),
    "tie-break collision old room absent": (("RM-01", "CL-A", "2026-03-12"), None),
    # Half-open boundary at midnight (sections 5, 7, 9, 10): BK-2303's rev2
    # tie-break picks the BOOKED [23:00,00:00) row. The interval is half-open,
    # so its 60 minutes belong to 2026-03-13 only; nothing leaks into
    # 2026-03-14.
    "midnight boundary day 1": (("RM-01", "CL-B", "2026-03-13"), 60),
    "midnight boundary no next-day leak": (("RM-01", "CL-B", "2026-03-14"), None),
    # Overlap merge (sections 11, 12): BK-2304 [09:00,10:30), BK-2305
    # [10:00,11:00) and BK-2306 [10:15,10:45) share RM-04 on 2026-03-14; the
    # room-wide union is [09:00,11:00) = 120 minutes regardless of provider.
    "overlapping intervals merge to 120": (("RM-04", "CL-A", "2026-03-14"), 120),
    # Dedup/validation -> collision -> effective revision (sections 4, 5, 7,
    # 16): BK-2307's rev2 VOIDED event survives as its valid first copy and
    # ties with the BOOKED row on created_ts; the greater event_id (VOIDED)
    # wins the collision and is skipped, so the effective rev1 [09:00,10:00)
    # on 2026-03-15 is single-day and nothing appears on 2026-03-16.
    "dedup collision falls back to rev1": (("RM-01", "CL-C", "2026-03-15"), 60),
    "dedup collision no next-day footprint": (("RM-01", "CL-C", "2026-03-16"), None),
    # Touching intervals (sections 11, 12): BK-2308 [09:00,10:00) and BK-2309
    # [10:00,11:00) merely touch, so their union is 120 minutes and the
    # room-day is NOT overlapping.
    "touching intervals union": (("RM-02", "CL-B", "2026-03-16"), 120),
    # Seconds truncation (sections 3, 9): BK-2310 starts at 23:59:30; seconds
    # are truncated to the minute, leaving a single blocked minute [23:59,
    # 00:00) on 2026-03-17 and nothing on 2026-03-18.
    "seconds truncated to the minute": (("RM-01", "CL-A", "2026-03-17"), 1),
    "seconds interval stays on start day": (("RM-01", "CL-A", "2026-03-18"), None),
}


@pytest.mark.parametrize("label", sorted(CROSS_STAGE_CELLS))
def test_cross_stage_cells(facts_lookup, label):
    (room, clinic, date), expected = CROSS_STAGE_CELLS[label]
    key = (room, clinic, date)
    actual = facts_lookup.get(key)
    assert actual == expected, \
        "%s: %r = %r, expected %r" % (label, key, actual, expected)
