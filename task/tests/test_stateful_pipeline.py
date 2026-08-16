"""Stateful operational-invariant verifier for the room-capacity pipeline.

Drives the submitted multi-component pipeline (ingest_events.py,
rebuild_state.py, prepare_facts.py) through the operational execution modes
of contract Section 30 and checks that every valid execution history of the
same logical event universe produces byte-identical final artifacts.

Expected artifacts are derived independently (hidden_reference) from the raw
logical universe; they are never produced by the submitted implementation.

The components are discovered from the grading lifecycle:
  * oracle validation: /solution/bin/* (reference components);
  * agent runs: /app/bin/* (the agent's repaired components).

Every scenario uses a fresh temporary state directory and a fresh temporary
output directory; no state is shared between independent cases, and the
public /app/output directory is never touched.
"""

import csv
import json
import os
import shutil
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import hidden_reference
import hidden_universes

OUTPUT_NAMES = ["effective_bookings.csv", "booking_state_history.csv",
                "daily_room_capacity_facts.csv",
                "capacity_reconciliation.csv", "quality_summary.json"]


def _components_root():
    oracle = os.path.join(os.sep, "solution", "bin", "ingest_events.py")
    if os.path.isfile(oracle):
        return os.path.join(os.sep, "solution", "bin")
    return os.path.join(os.sep, "app", "bin")


def _run(script, args):
    path = os.path.join(_components_root(), script)
    proc = subprocess.run([sys.executable, path] + args, capture_output=True,
                          text=True)
    assert proc.returncode == 0, (
        "%s failed:\n%s" % (path, proc.stderr[-2000:]))
    return proc.stdout


def _ingest(state, batch_dir):
    _run("ingest_events.py", ["--state", state,
                              "--scheduling",
                              os.path.join(batch_dir, "scheduling_events.csv"),
                              "--adjustments",
                              os.path.join(batch_dir,
                                           "reservation_adjustments.ndjson"),
                              "--clinics",
                              os.path.join(batch_dir, "clinic_directory.csv"),
                              "--rooms",
                              os.path.join(batch_dir, "room_directory.csv")])


def _rebuild(state):
    _run("rebuild_state.py", ["--state", state])


def _prepare(state, out, replay=False):
    args = ["--state", state, "--output", out]
    if replay:
        args.append("--replay")
    _run("prepare_facts.py", args)


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


def _split_universe(universe, frac=0.5):
    """Return (a, b) disjoint partitions of the scheduling/adjustments."""
    n = len(universe["scheduling"])
    k = max(1, int(n * frac))
    sched_a = universe["scheduling"][:k]
    sched_b = universe["scheduling"][k:]
    adj = universe["adjustments"]
    m = len(adj)
    j = max(0, int(m * frac))
    adj_a = adj[:j]
    adj_b = adj[j:]
    a = dict(universe)
    a["scheduling"] = sched_a
    a["adjustments"] = adj_a
    b = dict(universe)
    b["scheduling"] = sched_b
    b["adjustments"] = adj_b
    return a, b


def _read_artifacts(out):
    arts = {}
    for n in OUTPUT_NAMES:
        if n == "quality_summary.json":
            with open(os.path.join(out, n), encoding="utf-8") as h:
                arts[n] = json.load(h)
            continue
        with open(os.path.join(out, n), encoding="utf-8", newline="") as h:
            rows = list(csv.reader(h))
        arts[n] = rows[1:]  # strip header
    return arts


def _fresh(state, out, batch_dir):
    _ingest(state, batch_dir)
    _rebuild(state)
    _prepare(state, out)
    return _read_artifacts(out)


def _scenario_repeated_ingest(state, out, batch_dir):
    _ingest(state, batch_dir)
    _ingest(state, batch_dir)
    _rebuild(state)
    _prepare(state, out)
    return _read_artifacts(out)


def _scenario_split_batch(state, out, a_dir, b_dir):
    _ingest(state, a_dir)
    _ingest(state, b_dir)
    _rebuild(state)
    _prepare(state, out)
    return _read_artifacts(out)


def _scenario_restart(state, out, a_dir, b_dir):
    _ingest(state, a_dir)
    _rebuild(state)
    state2 = state + ".resume"
    shutil.copy(state, state2)
    _ingest(state2, b_dir)
    _rebuild(state2)
    _prepare(state2, out)
    return _read_artifacts(out)


def _scenario_replay(state, out, batch_dir):
    _ingest(state, batch_dir)
    _rebuild(state)
    _prepare(state, out, replay=True)
    return _read_artifacts(out)


def _scenario_revision_update(state, out, a_dir, b_dir):
    _ingest(state, a_dir)
    _rebuild(state)
    _prepare(state, out)
    _ingest(state, b_dir)
    _rebuild(state)
    _prepare(state, out)
    return _read_artifacts(out)


def _scenario_partitions(state, out, a_dir, b_dir):
    # [A][B], [A][empty][B], [A][B][dup B]
    _ingest(state, a_dir)
    _ingest(state, b_dir)
    _ingest(state, b_dir)  # duplicate re-ingest of B
    _rebuild(state)
    _prepare(state, out)
    return _read_artifacts(out)


def _scenario_cross_source(state, out, sched_dir, adj_dir):
    # scheduling and adjustments arrive in different batches
    _ingest(state, sched_dir)
    _ingest(state, adj_dir)
    _rebuild(state)
    _prepare(state, out)
    return _read_artifacts(out)


@pytest.fixture(scope="module")
def stateful_universes(tmp_path_factory):
    base = tmp_path_factory.mktemp("stateful_inputs")
    out = {}
    for name in hidden_universes.UNIVERSE_NAMES:
        u = hidden_universes.generate(name)
        full = base / (name + "_full")
        full.mkdir()
        _write_universe(str(full), u)
        a, b = _split_universe(u)
        a_dir = base / (name + "_a")
        b_dir = base / (name + "_b")
        a_dir.mkdir()
        b_dir.mkdir()
        _write_universe(str(a_dir), a)
        _write_universe(str(b_dir), b)
        sched_only = dict(u)
        sched_only["adjustments"] = []
        adj_only = dict(u)
        adj_only["scheduling"] = []
        sched_dir = base / (name + "_sched")
        adj_dir = base / (name + "_adj")
        sched_dir.mkdir()
        adj_dir.mkdir()
        _write_universe(str(sched_dir), sched_only)
        _write_universe(str(adj_dir), adj_only)
        out[name] = {"full": str(full), "a": str(a_dir), "b": str(b_dir),
                     "sched": str(sched_dir), "adj": str(adj_dir)}
    return out


def _grade(state, out, produced, expected):
    for n in OUTPUT_NAMES:
        assert produced[n] == expected[n], (
            "stateful %s mismatch for %s" % (n, out))


@pytest.mark.parametrize("scenario", [
    "fresh", "repeated_ingest", "split_batch", "restart", "replay",
    "revision_update", "partitions", "cross_source",
])
@pytest.mark.parametrize("uname", hidden_universes.UNIVERSE_NAMES)
def test_stateful_convergence(scenario, uname, stateful_universes, tmp_path):
    u = stateful_universes[uname]
    expected_dir = u["full"]
    expected = hidden_reference._expected(expected_dir)

    state = str(tmp_path / ("state_%s_%s.json" % (uname, scenario)))
    out = str(tmp_path / ("out_%s_%s" % (uname, scenario)))
    os.makedirs(out)

    scn = {
        "fresh": lambda s, o: _fresh(s, o, u["full"]),
        "repeated_ingest": lambda s, o: _scenario_repeated_ingest(
            s, o, u["full"]),
        "split_batch": lambda s, o: _scenario_split_batch(
            s, o, u["a"], u["b"]),
        "restart": lambda s, o: _scenario_restart(s, o, u["a"], u["b"]),
        "replay": lambda s, o: _scenario_replay(s, o, u["full"]),
        "revision_update": lambda s, o: _scenario_revision_update(
            s, o, u["a"], u["b"]),
        "partitions": lambda s, o: _scenario_partitions(s, o, u["a"], u["b"]),
        "cross_source": lambda s, o: _scenario_cross_source(
            s, o, u["sched"], u["adj"]),
    }[scenario]

    produced = scn(state, out)
    _grade(state, out, produced, expected)
