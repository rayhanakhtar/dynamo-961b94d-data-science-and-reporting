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
TOTAL_BLOCKED_MINUTES = 55159


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
    assert len(rows) == 270, "expected 270 fact rows, got %d" % len(rows)


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


# ---------------------------------------------------------------------------
# 12. Atomic semantic checks — state-propagation calibration fixtures
#     (fact_contract.md sections 3-17)
# ---------------------------------------------------------------------------
#
# Each cell pins a downstream consequence that is correct only when the
# authoritative record is traced through dedup -> validation -> collision ->
# effective revision -> attribution -> day materialization. These fixtures
# are designed so that several plausible intermediate states produce
# superficially reasonable outputs; only the contract-determined state chain
# yields these exact values.

STATE_PROPAGATION_CELLS = {
    # Family A — competing lineage (sections 5, 7, 8, 12): BK-2401's rev2
    # collision winner is the BOOKED RM-02 row (created_ts 11:00 beats the
    # VOIDED 10:00 row); the later VOIDED rev3 is skipped, so the effective
    # [14:00,15:30) on RM-02 merges with BK-2402 into 90 minutes. The
    # superseded RM-01 lineage emits nothing.
    "competing lineage lands on RM-02": (("RM-02", "CL-A", "2026-03-20"), 90),
    "competing lineage old room absent": (("RM-01", "CL-A", "2026-03-20"), None),
    # Family B — dedup survivor changes collision state (sections 4, 5, 16):
    # BK-2403's rev2 VOIDED event has a malformed first copy, so it is set
    # aside at validation and takes no part in the collision; the BOOKED
    # [10:00,11:00) row is authoritative, unioning with BK-2404 into 60
    # minutes on RM-03.
    "dedup survivor collision union": (("RM-03", "CL-A", "2026-03-21"), 60),
    # Family C — summary population vs fact population (sections 5, 13, 16,
    # 17): both BK-2405 and BK-2406 resolve to a BOOKED rev2 winner on
    # 2026-03-22. In BK-2405 the VOIDED rev2 loser is well-formed (it
    # contributes to voided_revisions but not to capacity); in BK-2406 the
    # analogous loser is malformed and contributes to neither.
    "summary-vs-fact population CL-B": (("RM-01", "CL-B", "2026-03-22"), 60),
    "summary-vs-fact population CL-C": (("RM-01", "CL-C", "2026-03-22"), 60),
    # Family D — day materialization depends on effective state (sections 7,
    # 10, 12): BK-2407's VOIDED rev3 is skipped; the effective BOOKED rev2
    # occupies RM-04 [10:00,11:00) on 2026-03-24. The superseded rev1
    # midnight-crossing interval on RM-02 (2026-03-23/24) emits nothing.
    "effective state source day": (("RM-04", "CL-A", "2026-03-24"), 60),
    "effective state superseded day absent": (("RM-02", "CL-A", "2026-03-23"), None),
    "effective state superseded day absent 2": (("RM-02", "CL-A", "2026-03-24"), None),
    # Family E — aggregation after state resolution (sections 7, 11, 12):
    # BK-2408's superseded rev1 [09:00,11:00) is dropped; the union of the
    # three active effective intervals on RM-04 is [10:00,13:00) = 180
    # minutes.
    "union of effective intervals only": (("RM-04", "CL-A", "2026-03-25"), 180),
    # Family F — near-miss boundary (sections 10, 17): BK-2411's effective
    # BOOKED rev2 is [23:00,00:00); its 60 minutes belong to 2026-03-26 only,
    # and the booking is NOT multi-day. Nothing leaks into 2026-03-27.
    "midnight-ending effective interval": (("RM-01", "CL-A", "2026-03-26"), 60),
    "midnight-ending effective no leak": (("RM-01", "CL-A", "2026-03-27"), None),
}


# ---------------------------------------------------------------------------
# 13. Atomic semantic checks — provenance-partition calibration fixtures
#     (fact_contract.md section 17)
# ---------------------------------------------------------------------------
# Each cell exercises the requirement that every quality-summary metric is
# computed over its contract-defined population, and that fact-table cells
# depend only on the final room-day union population.
#
PROVENANCE_PARTITION_CELLS = {
    # Family A — validation changes collision population (sections 4, 5, 16)
    # BK-2501: rev2 collision has a malformed row (ev 999, VOIDED [11:00,10:00),
    # end before start) and a valid BOOKED row (ev 998, [10:00,11:00));
    # validation removes the malformed row, so collision selects the valid
    # BOOKED row and effective rev2 BOOKED [10:00,11:00) = 60. The later VOIDED
    # rev3 is skipped; the superseded rev1 is not effective.
    # BK-2502 overlaps [10:15,10:45) -> union [10:00,11:00) = 60.
    # Wrong pipeline (collision before validation) picks the malformed row -> 0.
    "A collision winner valid after validation": (("RM-01", "CL-A", "2026-03-28"), 60),
    # Family B — collision loser vs validation loser provenance (sections 5, 16, 17)
    # BK-2503: rev2 has valid BOOKED [11:00,12:00) and VOIDED [10:00,11:00)
    # collision loser -> VOIDED counts in voided_revisions but not capacity.
    # BK-2504: rev2 has BOOKED [11:00,12:00) and MALFORMED VOIDED [10:00,09:00)
    # collision loser -> malformed removed before collision -> no voided count.
    # Both yield 60 mins on RM-02/CL-B and RM-02/CL-C 2026-03-28.
    "B1 well-formed voided collision loser": (("RM-02", "CL-B", "2026-03-28"), 60),
    "B2 malformed voided collision loser": (("RM-02", "CL-C", "2026-03-28"), 60),
    # Family C — effective-state provenance (sections 5, 7, 9, 10, 12)
    # BK-2505: rev1 [23:00,00:30) multi-day, rev2 BOOKED [10:00,11:00) on RM-03,
    # rev2 VOIDED [11:00,12:00) collision loser, rev2 BOOKED [10:00,12:00) on RM-04
    # -> effective rev2 BOOKED on RM-04 (10:00-12:00) = 120 on 2026-03-30.
    # Neighbor BK-2506 on RM-03 10:30-11:30 -> 60 mins.
    # Superseded rev1 midnight interval must NOT leak into 2026-03-30 on RM-03.
    "C effective room RM-04 not RM-03": (("RM-04", "CL-A", "2026-03-30"), 120),
    "C neighbor effective on RM-03": (("RM-03", "CL-A", "2026-03-29"), 60),
    "C superseded midnight no leak": (("RM-03", "CL-A", "2026-03-30"), None),
    # Family D — cancellation vs no-effective-state distinction (sections 7, 8, 17)
    # BK-2507: rev2 CANCELLED -> cancelled_bookings, not active, no fact row.
    # BK-2508: all VOIDED -> no_eff_revision, no fact row.
    # BK-2509: rev2 malformed [10:00,09:00) removed -> effective rev1 BOOKED [09:00,10:00).
    # BK-2510: rev2 VOIDED -> effective rev1 BOOKED [09:00,10:00).
    "D cancelled no fact": (("RM-01", "CL-A", "2026-03-29"), None),
    "D all-voided no fact": (("RM-01", "CL-B", "2026-03-29"), None),
    "D malformed-latest -> earlier active": (("RM-01", "CL-C", "2026-03-29"), 60),
    "D VOIDED-latest -> earlier active": (("RM-02", "CL-A", "2026-03-29"), 60),
    # Family E — materialization provenance (sections 7, 10, 11, 12)
    # BK-2511: rev2 BOOKED [23:00,00:00) 60 mins on 2026-03-30; rev3 VOIDED skipped.
    # Neighbor BK-2512 on RM-03 [00:15,00:45) = 30 mins on 2026-03-31.
    # Union on RM-03 CL-B 2026-03-30 = 60; 2026-03-31 = 30.
    "E effective midnight interval": (("RM-03", "CL-B", "2026-03-30"), 60),
    "E neighbor same room next day": (("RM-03", "CL-B", "2026-03-31"), 30),
    # Family F — summary-only population trap (sections 16, 17)
    # BK-2514: rev1 BOOKED [09:00,10:00) is effective; rev2 has a malformed
    # BOOKED row (dropped at validation) and a VOIDED row, so rev2 is VOIDED
    # and contributes nothing -> 60 on RM-01 CL-B 2026-03-30.
    # BK-2515: rev1 has a malformed BOOKED row (dropped) and a VOIDED row;
    # with the malformed row gone, the sole authoritative rev1 row is VOIDED,
    # so the booking has no effective revision. Fact table empty; but
    # voided_revisions counts the valid VOIDED row.
    "F malformed BOOKED latest -> rev1 active": (("RM-01", "CL-B", "2026-03-30"), 60),
    "F all-voided no fact": (("RM-02", "CL-B", "2026-03-30"), None),
    # Family G — cross-provider effective-state union (sections 7, 11, 12)
    # RM-04 CL-A 2026-03-31: BK-2516 [10:00,12:00), BK-2517 [10:30,11:30), BK-2518 [12:00,13:00)
    # all active effective -> union [10:00,13:00) = 180 mins.
    "G cross-provider union on RM-04": (("RM-04", "CL-A", "2026-03-31"), 180),
    # Family H — multi-day boundary / provenance interaction (sections 10, 12, 16)
    # BK-2519: rev1 BOOKED [23:00,00:30) on RM-01 CL-C (multi-day).
    # rev2 BOOKED [23:59:30,00:00) on RM-02 CL-C (single-day, 30 sec -> 1 min).
    # rev3 VOIDED skipped.
    # Effective room is RM-02 (rev2). RM-01 has no active effective.
    # Seconds truncation: 23:59:30 -> 23:59 -> 1 min on 2026-03-30.
    "H seconds truncation single-day": (("RM-02", "CL-C", "2026-03-30"), 1),
    "H superseded midnight room absent": (("RM-01", "CL-C", "2026-03-30"), None),
    "H seconds no next-day leak": (("RM-02", "CL-C", "2026-03-31"), None),
}


@pytest.mark.parametrize("label", sorted(STATE_PROPAGATION_CELLS))
def test_state_propagation_cells(facts_lookup, label):
    (room, clinic, date), expected = STATE_PROPAGATION_CELLS[label]
    key = (room, clinic, date)
    actual = facts_lookup.get(key)
    assert actual == expected, \
        "%s: %r = %r, expected %r" % (label, key, actual, expected)


@pytest.mark.parametrize("label", sorted(PROVENANCE_PARTITION_CELLS))
def test_provenance_partition_cells(facts_lookup, label):
    (room, clinic, date), expected = PROVENANCE_PARTITION_CELLS[label]
    key = (room, clinic, date)
    actual = facts_lookup.get(key)
    assert actual == expected, \
        "%s: %r = %r, expected %r" % (label, key, actual, expected)


# ---------------------------------------------------------------------------
# 14. Atomic semantic checks - non-contiguous revision numbering
#     (fact_contract.md sections 5, 7, 10, 12)
# ---------------------------------------------------------------------------
#
# Each cell fixes the outcome when a booking's revisions skip numbers.
# Revision numbers need not be contiguous (section 5); the effective revision
# is still the first non-voided revision found scanning downward by revision
# value (section 7), and it alone fixes the booking's room, clinic, and
# interval. The superseded revision emits nothing and the old room or day
# carries no blocked minutes.

REVISION_GAP_CELLS = {
    # Family A, revision-gap rebooking across rooms (sections 5, 7, 12):
    # BK-3001's revisions are 1 and 3. The effective revision is 3, which is
    # booked on RM-06, so 60 minutes land on RM-06/CL-A 2026-04-06 and the
    # superseded rev1 room RM-05 emits nothing.
    "gap rebook lands on latest room": (("RM-06", "CL-A", "2026-04-06"), 60),
    "gap rebook old room absent": (("RM-05", "CL-A", "2026-04-06"), None),
    # Family B, revision-gap rebooking across days (sections 5, 7, 10):
    # BK-3002's effective rev3 moves the same room's block from 2026-04-07 to
    # 2026-04-08; the earlier date is no longer blocked.
    "gap rebook lands on latest day": (("RM-05", "CL-B", "2026-04-08"), 60),
    "gap rebook old day absent": (("RM-05", "CL-B", "2026-04-07"), None),
    # Family C, contiguous-revision control (sections 5, 7): BK-3003 uses the
    # contiguous revisions 1 and 2, so the effective rev2 moves the block to
    # 2026-04-10 exactly as the contract requires.
    "contiguous control latest day": (("RM-06", "CL-B", "2026-04-10"), 60),
    "contiguous control old day absent": (("RM-06", "CL-B", "2026-04-09"), None),
    # Family D, voided revision with a numbering gap (sections 5, 6, 7):
    # BK-3004's rev3 is voided and therefore skipped, so the effective rev1
    # keeps the room blocked on 2026-04-11.
    "voided-gap control active": (("RM-05", "CL-C", "2026-04-11"), 60),
    # Phase 21 Family A: the booking's effective state is cancelled even though
    # its latest revision is voided; the cancelled state contributes nothing, so
    # only the neighbor [10:30,11:30) remains.
    "phase21 cancelled effective emits nothing": (("RM-07", "CL-A", "2026-04-12"), 60),
    # Phase 21 Family C: the cancelled booking's old day contributes nothing.
    "phase21 composed cancelled day absent": (("RM-08", "CL-A", "2026-04-14"), None),
    "phase21 composed survivor day": (("RM-08", "CL-A", "2026-04-15"), 60),
}


@pytest.mark.parametrize("label", sorted(REVISION_GAP_CELLS))
def test_revision_gap_cells(facts_lookup, label):
    (room, clinic, date), expected = REVISION_GAP_CELLS[label]
    key = (room, clinic, date)
    actual = facts_lookup.get(key)
    assert actual == expected, \
        "%s: %r = %r, expected %r" % (label, key, actual, expected)


# ---------------------------------------------------------------------------
# 15. Independent semantic invariants (composed / cross-stage verification)
# ---------------------------------------------------------------------------
#
# These checks independently derive expected room-day capacity from the PUBLIC
# input export (/app/data/scheduling_events.csv, which the verifier may read)
# and verify the agent's fact table satisfies the contract's semantic
# invariants. They are NOT a re-run of the agent's code; they re-derive the
# effective-state -> materialization -> union chain from the raw rows and
# assert the produced artifacts agree with it. This makes the composed
# families (BK-4101..BK-4502) verified as held-out scenarios rather than as
# transparently named cells.

import datetime as _datetime
import collections as _collections

# The public export is available to the verifier at verify time; it is also
# overridable (e.g. during local development) via the environment.
DATA_INPUT = os.environ.get("DYNAMO_INPUT_DATA", "/app/data/scheduling_events.csv")


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


def _load_public_rows():
    """Parse + dedup + validate the public export exactly per the contract."""
    if not os.path.exists(DATA_INPUT):
        return []
    with open(DATA_INPUT, encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        raw = list(reader)
    seen = set()
    valid = []
    for row in raw:
        if len(row) < 13:
            continue
        event_id = row[0].strip()
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        booking_id = row[1].strip()
        try:
            revision = int(row[2].strip())
            assert revision >= 0
        except Exception:
            continue
        status = row[3].strip().upper()
        clinic = row[4].strip().upper()
        room = row[5].strip().upper()
        if status not in ("BOOKED", "CANCELLED", "VOIDED") or not clinic or not room:
            continue
        if not booking_id:
            continue
        try:
            sd = _datetime.date.fromisoformat(row[8].strip())
            ed = _datetime.date.fromisoformat(row[10].strip())
            sm = _parse_clock(row[9])
            em = _parse_clock(row[11])
            created_ts = _datetime.datetime.strptime(
                row[12].strip(), "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if sm is None or em is None:
            continue
        start = _datetime.datetime.combine(sd, _datetime.time()) + _datetime.timedelta(minutes=sm)
        end = _datetime.datetime.combine(ed, _datetime.time()) + _datetime.timedelta(minutes=em)
        if end < start:
            continue
        valid.append({"booking_id": booking_id, "revision": revision,
                      "status": status, "clinic_id": clinic, "room_id": room,
                      "event_id": event_id, "created_ts": created_ts,
                      "start": start, "end": end})
    return valid


def _effective_by_booking(rows):
    """Collision resolve (created_ts, event_id) then effective-scan descending
    skipping VOIDED, per contract sections 5 and 7."""
    by_booking = _collections.defaultdict(dict)
    for r in rows:
        by_booking[r["booking_id"]].setdefault(r["revision"], []).append(r)
    authoritative = {}
    for bid, revisions in by_booking.items():
        for rev, events in revisions.items():
            authoritative[(bid, rev)] = max(
                events, key=lambda e: (e["created_ts"], e["event_id"]))
    result = {}
    for bid, revisions in by_booking.items():
        chosen = None
        for rev in sorted(revisions, reverse=True):
            cand = authoritative[(bid, rev)]
            if cand["status"] != "VOIDED":
                chosen = cand
                break
        result[bid] = chosen
    return result


def _day_segments(rec):
    out = []
    day_start = _datetime.datetime.combine(rec["start"].date(), _datetime.time())
    while day_start < rec["end"]:
        day_end = day_start + _datetime.timedelta(days=1)
        lo = rec["start"] if rec["start"] > day_start else day_start
        hi = rec["end"] if rec["end"] < day_end else day_end
        if hi > lo:
            out.append((day_start.date(), lo, hi))
        day_start = day_end
    return out


def _union_minutes(iv):
    if not iv:
        return 0
    merged = []
    for s, e in sorted(iv):
        if merged and s < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return sum(int((e - s).total_seconds()) // 60 for s, e in merged)


def _independent_facts_lookup():
    """Return {room,clinic,day: minutes} recomputed from the public input."""
    rows = _load_public_rows()
    effective = _effective_by_booking(rows)
    day_timelines = _collections.defaultdict(list)
    for rec in effective.values():
        if rec is None or rec["status"] != "BOOKED":
            continue
        for day, lo, hi in _day_segments(rec):
            day_timelines[(rec["room_id"], rec["clinic_id"], day)].append((lo, hi))
    out = {}
    for (room, clinic, day), iv in day_timelines.items():
        minutes = _union_minutes(iv)
        if minutes > 0:
            out[(room, clinic, day.isoformat())] = minutes
    return out


def _normalize_row(row):
    """Normalize one raw export row per contract sections 3 and 16; return a
    record dict, or None if the row is malformed."""
    if len(row) < 13:
        return None
    event_id = row[0].strip()
    booking_id = row[1].strip()
    if not event_id or not booking_id:
        return None
    try:
        revision = int(row[2].strip())
        if revision < 0:
            return None
    except ValueError:
        return None
    status = row[3].strip().upper()
    clinic = row[4].strip().upper()
    room = row[5].strip().upper()
    if status not in ("BOOKED", "CANCELLED", "VOIDED") or not clinic or not room:
        return None
    try:
        sd = _datetime.date.fromisoformat(row[8].strip())
        ed = _datetime.date.fromisoformat(row[10].strip())
        sm = _parse_clock(row[9])
        em = _parse_clock(row[11])
        created_ts = _datetime.datetime.strptime(row[12].strip(),
                                                 "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    if sm is None or em is None:
        return None
    start = _datetime.datetime.combine(sd, _datetime.time()) + \
        _datetime.timedelta(minutes=sm)
    end = _datetime.datetime.combine(ed, _datetime.time()) + \
        _datetime.timedelta(minutes=em)
    if end < start:
        return None
    return {"booking_id": booking_id, "revision": revision, "status": status,
            "clinic_id": clinic, "room_id": room, "event_id": event_id,
            "created_ts": created_ts, "start": start, "end": end}


def _independent_summary():
    """Recompute all twelve quality-summary values from the public input alone,
    following the contract's population provenance (sections 4, 5, 7, 8, 9,
    10, 12, 16, 17). Each metric is sourced from the exact population its
    contract row specifies."""
    raw_rows = []
    if os.path.exists(DATA_INPUT):
        with open(DATA_INPUT, encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            raw_rows = list(reader)
    total_raw = len(raw_rows)

    # population 1 -> 2: deduplicate by event_id (first copy in file order wins)
    seen = set()
    surviving = []
    duplicates = 0
    for row in raw_rows:
        event_id = row[0].strip()
        if event_id in seen:
            duplicates += 1
            continue
        seen.add(event_id)
        surviving.append(row)

    # population 2 -> 3: validate the survivors
    validated = []
    malformed = 0
    for row in surviving:
        rec = _normalize_row(row)
        if rec is None:
            malformed += 1
        else:
            validated.append(rec)

    # population 3 metrics
    total_booking_ids = len({r["booking_id"] for r in validated})
    voided_revisions = sum(1 for r in validated if r["status"] == "VOIDED")

    # population 3 -> 4: collision resolution (latest created_ts, then
    # greatest event_id as a string)
    by_booking = _collections.defaultdict(dict)
    for r in validated:
        by_booking[r["booking_id"]].setdefault(r["revision"], []).append(r)
    authoritative = {}
    for bid, revisions in by_booking.items():
        for rev, events in revisions.items():
            authoritative[(bid, rev)] = max(
                events, key=lambda e: (e["created_ts"], e["event_id"]))

    # population 4 -> 5: effective revision (highest non-voided authoritative)
    effective = {}
    for bid, revisions in by_booking.items():
        chosen = None
        for rev in sorted(revisions, reverse=True):
            cand = authoritative[(bid, rev)]
            if cand["status"] != "VOIDED":
                chosen = cand
                break
        effective[bid] = chosen

    bookings_with_effective = sum(1 for e in effective.values() if e is not None)
    active_bookings = sum(1 for e in effective.values()
                          if e is not None and e["status"] == "BOOKED")
    cancelled_bookings = sum(1 for e in effective.values()
                             if e is not None and e["status"] == "CANCELLED")
    no_effective = sum(1 for e in effective.values() if e is None)

    # populations 5 -> 7 -> 8: day materialization, room-day union
    day_timelines = _collections.defaultdict(list)
    for rec in effective.values():
        if rec is None or rec["status"] != "BOOKED":
            continue
        for day, lo, hi in _day_segments(rec):
            day_timelines[(rec["room_id"], rec["clinic_id"], day)].append((lo, hi))

    total_blocked = 0
    overlap_days = 0
    for iv in day_timelines.values():
        union = _union_minutes(iv)
        summed = sum(int((e - s).total_seconds()) // 60 for s, e in iv)
        if union < summed:
            overlap_days += 1
        total_blocked += union

    multi_day = 0
    for rec in effective.values():
        if rec is None or rec["status"] != "BOOKED":
            continue
        if len(_day_segments(rec)) > 1:
            multi_day += 1

    return {
        "total_raw_events": total_raw,
        "duplicate_events_removed": duplicates,
        "malformed_rows_rejected": malformed,
        "total_booking_ids": total_booking_ids,
        "voided_revisions": voided_revisions,
        "bookings_with_effective_revision": bookings_with_effective,
        "active_bookings": active_bookings,
        "cancelled_bookings": cancelled_bookings,
        "bookings_with_no_effective_revision": no_effective,
        "multi_day_active_bookings": multi_day,
        "room_clinic_days_with_overlap": overlap_days,
        "total_blocked_minutes": total_blocked,
    }


@pytest.fixture(scope="module")
def independent_facts():
    return _independent_facts_lookup()


def test_composed_families_match_independent_semantics(facts_lookup, independent_facts):
    """For the composed families (BK-4101..BK-4502, on/after 2026-04-16), the
    agent's fact table must exactly match the independently-derived capacity.
    These are held-out scenarios, not transparently named cells: the agent must
    trace effective-state selection -> materialization -> union to get them."""
    for (room, clinic, day), minutes in independent_facts.items():
        if day < "2026-04-16":
            continue
        assert facts_lookup.get((room, clinic, day)) == minutes, \
            "composed family mismatch %r: got %r, independently derived %r" % (
                (room, clinic, day), facts_lookup.get((room, clinic, day)), minutes)
    # No extra rows for composed rooms/days beyond the derived ones.
    for (room, clinic, day), minutes in facts_lookup.items():
        if day < "2026-04-16":
            continue
        if room not in ("RM-09", "RM-10", "RM-11", "RM-12", "RM-13"):
            continue
        assert independent_facts.get((room, clinic, day)) == minutes, \
            "extra composed-family row %r = %r not derivable from input" % (
                (room, clinic, day), minutes)


def test_no_superseded_room_leakage(facts_lookup, independent_facts):
    """The effective revision fixes the booking's (clinic, room). A superseded
    revision's room/day must never be the SOLE support for a fact row. For
    every produced fact cell, there must be at least one ACTIVE EFFECTIVE
    booking (not a superseded revision) that occupies that room on that day."""
    rows = _load_public_rows()
    effective = _effective_by_booking(rows)
    # active effective occupancy per (room, clinic, day)
    active_occupancy = set()
    for bid, eff in effective.items():
        if eff is None or eff["status"] != "BOOKED":
            continue
        for day, _lo, _hi in _day_segments(eff):
            active_occupancy.add((eff["room_id"], eff["clinic_id"], day.isoformat()))
    # superseded-only occupancy (booked revisions that are NOT the effective row)
    for bid, eff in effective.items():
        if eff is None or eff["status"] != "BOOKED":
            continue
        for r in rows:
            if r["booking_id"] != bid or r["status"] != "BOOKED":
                continue
            if (r["clinic_id"], r["room_id"]) == (eff["clinic_id"], eff["room_id"]):
                continue
            for day, _lo, _hi in _day_segments(r):
                cell = (r["room_id"], r["clinic_id"], day.isoformat())
                if cell not in active_occupancy:
                    assert facts_lookup.get(cell) is None, \
                        "fact row %r is only supported by a superseded revision" % (cell,)
    # conversely, every active effective cell must appear in the fact table
    for cell in active_occupancy:
        room, clinic, day = cell
        assert facts_lookup.get(cell) is not None, \
            "missing fact row for active effective booking on %r" % (cell,)


def test_effective_room_attribution(facts_lookup, independent_facts):
    """Every active booking's effective interval contributes its blocked
    minutes on the effective revision's (room, clinic) only. This asserts the
    union-based capacity for the composed families is computed after
    effective-state resolution (sections 7, 11, 12)."""
    # The composed-family union check already covers the main interaction;
    # this additionally asserts that no row exists for the superseded rooms of
    # the new families (RM-09 superseded side is absent, etc.).
    for (room, clinic, day), minutes in independent_facts.items():
        if day < "2026-04-16":
            continue
        if facts_lookup.get((room, clinic, day)) != minutes:
            raise AssertionError(
                "composed family %r does not match independently-derived %r" % (
                    (room, clinic, day), minutes))


# ---------------------------------------------------------------------------
# 16. Independent semantic recomputation of the quality summary
# ---------------------------------------------------------------------------
#
# These checks re-derive every quality-summary value from the PUBLIC input
# alone and compare key-by-key, so the summary is verified against the
# contract's population provenance rather than only against hidden golden
# values or algebraic identities. Each metric is recomputed from the exact
# population its contract row specifies (deduplicated survivors, validated
# survivors, collision winners, effective revisions, effective-booked
# population, and the materialized fact population).

def test_summary_recomputed_independently():
    summary, _duplicates = read_summary(SUMMARY_PATH)
    expected = _independent_summary()
    for key in SUMMARY_KEYS:
        assert summary.get(key) == expected[key], \
            "%s = %r, independently recomputed %r" % (
                key, summary.get(key), expected[key])
