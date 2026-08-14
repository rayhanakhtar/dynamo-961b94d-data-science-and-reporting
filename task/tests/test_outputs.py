"""Dynamo-compatible verifier entrypoint for the multi-artifact room-capacity task.

The substantive verification suite is modular and lives alongside this file
(test_effective_bookings.py, test_history.py, test_daily_facts.py,
test_reconciliation.py, test_quality.py, test_cross_artifact.py,
test_anti_cheat.py, conftest.py). This module is the Dynamo static-check
compatibility entrypoint named ``test_outputs.py``. It does not duplicate the
modular suite; it provides a single artifact-integrity smoke check so that the
required verifier filename is present and non-empty, while all real grading
continues to be performed by the modular tests that ``test.sh`` collects.

This verifier never imports, executes, or re-runs the agent's
``prepare_facts.py`` or any solution module. It inspects only the five output
files the agent is asked to produce, and the hidden golden outputs under
``/tests/expected/``.
"""

import csv
import json
import os

from conftest import OUTPUT_DIR, OUTPUT_FILES, check_output_files

REQUIRED_OUTPUTS = [
    "effective_bookings.csv",
    "booking_state_history.csv",
    "daily_room_capacity_facts.csv",
    "capacity_reconciliation.csv",
    "quality_summary.json",
]


def test_all_required_outputs_present_and_readable():
    """The agent must produce all five required output artifacts as regular,
    readable files inside the real /app/output directory (symlink escapes are
    rejected by the shared validator; schema-level checks are performed by the
    modular suite)."""
    check_output_files()
    for name in REQUIRED_OUTPUTS:
        path = os.path.join(OUTPUT_DIR, name)
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        assert content.strip(), "output %s is empty" % name


def test_quality_summary_is_valid_json():
    """quality_summary.json must parse as a JSON object (no duplicate keys)."""
    path = os.path.join(OUTPUT_DIR, "quality_summary.json")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    seen = {}

    def pairs_hook(pairs):
        for key, _value in pairs:
            seen[key] = seen.get(key, 0) + 1
        return dict(pairs)

    summary = json.loads(text, object_pairs_hook=pairs_hook)
    duplicates = {key for key, count in seen.items() if count > 1}
    assert not duplicates, "duplicate JSON keys: %r" % sorted(duplicates)
    assert isinstance(summary, dict), "quality_summary is not an object"


def test_csv_outputs_are_not_empty():
    """Each CSV output must have a header and at least one data row."""
    for name in ("effective_bookings.csv", "booking_state_history.csv",
                 "daily_room_capacity_facts.csv",
                 "capacity_reconciliation.csv"):
        path = os.path.join(OUTPUT_DIR, name)
        with open(path, encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            rows = list(reader)
        assert header is not None, "%s missing header" % name
        assert rows, "%s has no data rows" % name
