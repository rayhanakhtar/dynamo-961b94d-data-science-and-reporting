"""Hidden semantic-generalization grader.

Runs the implementation under test against deterministic hidden input
universes generated from the documented contract semantics, and compares all
five produced artifacts exactly against an independent derivation.

The implementation under test is discovered from the grading lifecycle:

  * oracle validation: the reference solution is mounted at /solution
    (Harbor's OracleAgent uploads solution/ there and the verifier runs in
    the same container), so the oracle's own entrypoint (solution/solve.sh)
    is executed;
  * agent runs: the agent's repaired /app/prepare_facts.py is executed.

In both lifecycles the program is driven with the DYNAMO_* input/output
environment overrides, each variant writing into an isolated temp output
directory, so the public /app/output directory is never touched and the
hidden grader is fully independent of the public grader.

The hidden universes are generated into pytest temp directories at grading
time. They are never placed under the agent-visible /app/data directory, and
their values are never exposed to the agent environment.
"""

import csv
import json
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import hidden_reference
import hidden_universes

OUTPUT_NAMES = ["daily_room_capacity_facts.csv", "quality_summary.json",
                "effective_bookings.csv", "booking_state_history.csv",
                "capacity_reconciliation.csv"]


def _write_universe(input_dir, universe):
    with open(os.path.join(input_dir, "scheduling_events.csv"), "w",
              encoding="utf-8", newline="") as h:
        writer = csv.writer(h, lineterminator="\n")
        writer.writerow(["event_id", "booking_id", "revision", "status",
                         "clinic_id", "room_id", "provider_id", "source",
                         "start_date", "start_time", "end_date", "end_time",
                         "created_ts"])
        for r in universe["scheduling"]:
            writer.writerow([r["event_id"], r["booking_id"], r["revision"],
                             r["status"], r["clinic_id"], r["room_id"],
                             r["provider_id"], r["source"], r["start_date"],
                             r["start_time"], r["end_date"], r["end_time"],
                             r["created_ts"]])
    with open(os.path.join(input_dir, "reservation_adjustments.ndjson"), "w",
              encoding="utf-8") as h:
        for r in universe["adjustments"]:
            h.write(json.dumps(r) + "\n")
    with open(os.path.join(input_dir, "clinic_directory.csv"), "w",
              encoding="utf-8", newline="") as h:
        writer = csv.writer(h, lineterminator="\n")
        writer.writerow(["canonical_clinic_id", "clinic_alias"])
        for r in universe["clinic_directory"]:
            writer.writerow([r["canonical_clinic_id"], r["clinic_alias"]])
    with open(os.path.join(input_dir, "room_directory.csv"), "w",
              encoding="utf-8", newline="") as h:
        writer = csv.writer(h, lineterminator="\n")
        writer.writerow(["clinic_id", "room_alias", "canonical_room_id"])
        for r in universe["room_directory"]:
            writer.writerow([r["clinic_id"], r["room_alias"],
                             r["canonical_room_id"]])


def _read_csv(path):
    with open(path, encoding="utf-8", newline="") as h:
        return list(csv.reader(h))


def _read_summary(path):
    with open(path, encoding="utf-8") as h:
        return json.load(h)


def _header_for(out_name):
    return {
        "daily_room_capacity_facts.csv": ["room_id", "clinic_id",
                                          "calendar_date", "blocked_minutes"],
        "effective_bookings.csv": ["booking_id", "status", "clinic_id",
                                   "room_id", "start_ts", "end_ts",
                                   "provider_id", "source",
                                   "effective_revision"],
        "booking_state_history.csv": ["booking_id", "revision", "status",
                                      "clinic_id", "room_id", "start_ts",
                                      "end_ts", "effective_from",
                                      "effective_to", "is_effective"],
        "capacity_reconciliation.csv": ["room_id", "clinic_id",
                                        "calendar_date",
                                        "effective_booking_count",
                                        "raw_interval_minutes",
                                        "blocked_minutes",
                                        "overlap_minutes"],
    }[out_name]


def _program_under_test():
    """Return the graded program's absolute path.

    The verifier grades whichever implementation produced the artifacts under
    test. In oracle validation, Harbor's OracleAgent uploads solution/ to
    /solution and the verifier runs in the same container, so the oracle
    entrypoint is present; in agent runs, only the agent's /app/prepare_facts.py
    exists.
    """
    oracle_entry = os.path.join(os.sep, "solution", "solve.sh")
    if os.path.isfile(oracle_entry):
        return oracle_entry
    return os.path.join(os.sep, "app", "prepare_facts.py")


def _run_submitted(input_dir, out_dir, work_dir):
    """Run the implementation under test against the given input universe."""
    program = _program_under_test()
    env = dict(os.environ)
    # The verifier process runs under hardening flags (test.sh sets
    # PYTHONSAFEPATH/NOUSERSITE/NODEBUG etc.). Those flags must NOT leak into
    # the graded program's subprocess: PYTHONSAFEPATH in particular stops
    # Python from placing a script's own directory on sys.path, which would
    # break the reference solution's legitimate sibling-module imports (and
    # any single-file implementation that relies on its own directory). Drop
    # all PYTHON* variables so the graded program runs under normal Python
    # semantics.
    for key in [k for k in env if k.startswith("PYTHON")]:
        del env[key]
    env["DYNAMO_INPUT_SCHEDULING"] = os.path.join(input_dir,
                                                  "scheduling_events.csv")
    env["DYNAMO_INPUT_ADJUSTMENTS"] = os.path.join(
        input_dir, "reservation_adjustments.ndjson")
    env["DYNAMO_INPUT_CLINICS"] = os.path.join(input_dir,
                                               "clinic_directory.csv")
    env["DYNAMO_INPUT_ROOMS"] = os.path.join(input_dir, "room_directory.csv")
    env["DYNAMO_LEDGER_PATH"] = os.path.join(work_dir, "canonical_events.csv")
    env["DYNAMO_EFFECTIVE_PATH"] = os.path.join(out_dir,
                                                "effective_bookings.csv")
    env["DYNAMO_HISTORY_PATH"] = os.path.join(out_dir,
                                              "booking_state_history.csv")
    env["DYNAMO_RECONCILIATION_PATH"] = os.path.join(
        out_dir, "capacity_reconciliation.csv")
    env["DYNAMO_FACTS_PATH"] = os.path.join(out_dir,
                                            "daily_room_capacity_facts.csv")
    env["DYNAMO_SUMMARY_PATH"] = os.path.join(out_dir, "quality_summary.json")
    if program.endswith("solve.sh"):
        # The oracle entrypoint changes into its own directory and invokes
        # solve.py; run it with a workdir that keeps its sibling modules
        # importable.
        cwd = os.path.join(os.sep, "solution")
        cmd = ["/bin/bash", program]
    else:
        cwd = input_dir
        cmd = [sys.executable, program]
    proc = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, timeout=120)
    return proc


def _compare_one(produced, expected, label):
    assert produced == expected, (
        "%s mismatch:\nproduced=%r\nexpected=%r" % (label, produced, expected))


def _grade_variant(name, input_dir, tmp_path):
    out_dir = tmp_path / (name + "_out")
    work_dir = tmp_path / (name + "_work")
    out_dir.mkdir()
    work_dir.mkdir()
    proc = _run_submitted(input_dir, str(out_dir), str(work_dir))
    assert proc.returncode == 0, (
        "submitted implementation failed on %s: %s"
        % (name, proc.stderr.decode("utf-8", "replace")))

    expected = hidden_reference._expected(input_dir)

    for out_name in OUTPUT_NAMES:
        path = os.path.join(str(out_dir), out_name)
        assert os.path.exists(path), "missing %s in %s" % (out_name, name)
        assert not os.path.islink(path), "symlink %s in %s" % (out_name, name)
        assert os.path.isfile(path), "not a regular file %s" % path

    for out_name in OUTPUT_NAMES:
        if out_name == "quality_summary.json":
            produced = _read_summary(os.path.join(str(out_dir), out_name))
            exp = expected[out_name]
            _compare_one(produced, exp, "%s/%s" % (name, out_name))
        else:
            produced = _read_csv(os.path.join(str(out_dir), out_name))
            exp = expected[out_name]
            assert produced[0] == _header_for(out_name), (
                "header mismatch for %s/%s: %r" % (name, out_name, produced[0]))
            _compare_one(produced[1:], exp, "%s/%s" % (name, out_name))

    # No unexpected extra regular files in the hidden output directory.
    extra = [e for e in os.listdir(str(out_dir))
             if e not in OUTPUT_NAMES
             and os.path.isfile(os.path.join(str(out_dir), e))
             and not os.path.islink(os.path.join(str(out_dir), e))]
    assert not extra, "unexpected output files for %s: %r" % (name, extra)


@pytest.fixture(scope="module")
def hidden_inputs(tmp_path_factory):
    base = tmp_path_factory.mktemp("hidden_inputs")
    dirs = {}
    for name in hidden_universes.UNIVERSE_NAMES:
        d = base / name
        d.mkdir()
        _write_universe(str(d), hidden_universes.generate(name))
        dirs[name] = str(d)
    return dirs


@pytest.mark.parametrize("name", hidden_universes.UNIVERSE_NAMES)
def test_hidden_universe(name, hidden_inputs, tmp_path):
    _grade_variant(name, hidden_inputs[name], tmp_path)
