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
import _jail

OUTPUT_NAMES = ["effective_bookings.csv", "booking_state_history.csv",
                "daily_room_capacity_facts.csv",
                "capacity_reconciliation.csv", "quality_summary.json"]


def _components_root():
    oracle = os.path.join(os.sep, "solution", "bin", "ingest_events.py")
    if os.path.isfile(oracle):
        return os.path.join(os.sep, "solution", "bin")
    return os.path.join(os.sep, "app", "bin")


class _StatefulCtx(object):
    """Binds the stateful verifier to one grading lifecycle.

    Oracle lifecycle: components run directly with /solution available
    (trusted reference). Agent lifecycle: components run inside a chroot jail
    that contains only the submitted /app program, the generated hidden inputs
    (copied from ``input_base``), and empty output/work/state dirs -- so the
    submitted program cannot read /tests or /solution.
    """

    def __init__(self, jail, input_base, universes):
        self.jail = jail
        self.input_base = input_base
        self.universes = universes
        self.oracle = os.path.isfile(
            os.path.join(os.sep, "solution", "bin", "ingest_events.py"))

    def _translate(self, arg):
        if arg.startswith(self.input_base):
            return os.path.join("/input",
                                os.path.relpath(arg, self.input_base))
        if arg.startswith(self.jail.root):
            return self.jail.jail(arg)
        return arg

    def run(self, script, args):
        if self.oracle:
            path = os.path.join(_components_root(), script)
            proc = subprocess.run([sys.executable, path] + args,
                                  capture_output=True, text=True)
        else:
            translated = [self._translate(a) for a in args]
            # The verifier (root) may have pre-created output dirs / state
            # files inside the jail tree; the jailed program runs as nobody and
            # must be able to write them. Hidden inputs (/input) stay read-only.
            for arg in translated:
                if (arg.startswith(("/state", "/output", "/work"))
                        and not arg.startswith("/app")):
                    host_arg = self.jail.host(arg)
                    if os.path.isdir(host_arg):
                        os.chmod(host_arg, 0o1777)
                    elif os.path.isfile(host_arg):
                        os.chmod(host_arg, 0o666)
            proc = self.jail.run(
                [self.jail.interp, os.path.join("/app", "bin", script)]
                + translated, _jail.prepare_env_for_jail())
        assert proc.returncode == 0, (
            "%s failed:\n%s" % (script, proc.stderr[-2000:]))
        return proc.stdout

    def ingest(self, state, batch_dir):
        self.run("ingest_events.py",
                 ["--state", state, "--scheduling",
                  os.path.join(batch_dir, "scheduling_events.csv"),
                  "--adjustments",
                  os.path.join(batch_dir, "reservation_adjustments.ndjson"),
                  "--clinics",
                  os.path.join(batch_dir, "clinic_directory.csv"),
                  "--rooms",
                  os.path.join(batch_dir, "room_directory.csv")])

    def rebuild(self, state):
        self.run("rebuild_state.py", ["--state", state])

    def prepare(self, state, out, replay=False):
        args = ["--state", state, "--output", out]
        if replay:
            args.append("--replay")
        self.run("prepare_facts.py", args)


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


def _expected_derived_state(ledger):
    """Independently recompute authoritative/effective from a canonical
    ledger, using only the contract semantics (sections 5 and 7).

    Returns {"effective": {booking: event_id or None},
             "authoritative": {"booking|rev": event_id}}.
    """
    sorted_ledger = sorted(ledger, key=lambda r: r["event_id"])
    authoritative = {}
    for rec in sorted_ledger:
        key = (rec["booking_id"], rec["revision"])
        prior = authoritative.get(key)
        if prior is None or (rec["created_ts"], rec["event_id"]) > \
                (prior["created_ts"], prior["event_id"]):
            authoritative[key] = rec
    by_booking = {}
    for rec in sorted_ledger:
        by_booking.setdefault(rec["booking_id"], set()).add(rec["revision"])
    effective = {}
    for bid, revisions in by_booking.items():
        chosen = None
        for revision in sorted(revisions, reverse=True):
            cand = authoritative.get((bid, revision))
            if cand is not None and cand["status"] != "VOIDED":
                chosen = cand
                break
        effective[bid] = chosen
    return {
        "effective": {bid: (rec["event_id"] if rec is not None else None)
                      for bid, rec in effective.items()},
        "authoritative": {"%s|%s" % (k[0], k[1]): v["event_id"]
                          for k, v in authoritative.items()},
    }


def _check_derived_state_converges(state_path, scenario):
    """Contract Section 30.3/30.5: after an incremental ingest + rebuild, the
    persisted derived state (effective/authoritative) must converge to a fresh
    recomputation over the complete persisted canonical ledger.

    This is an observable state-level assertion: it reads only the persisted
    state JSON (canonical_events, effective, authoritative, checkpoint) and
    does not depend on how rebuild_state was implemented (no assumption about
    a needs_rebuild flag or any optimization).
    """
    with open(state_path, encoding="utf-8") as h:
        state = json.load(h)
    ledger = state.get("canonical_events", [])
    expected = _expected_derived_state(ledger)
    assert state.get("effective") == expected["effective"], (
        "persisted effective diverges from fresh recompute after %s "
        "(incremental/restart state must converge per Section 30.3/30.5)"
        % scenario)
    assert state.get("authoritative") == expected["authoritative"], (
        "persisted authoritative diverges from fresh recompute after %s "
        "(incremental/restart state must converge per Section 30.3/30.5)"
        % scenario)


def _fresh(ctx, state, out, batch_dir):
    ctx.ingest(state, batch_dir)
    ctx.rebuild(state)
    ctx.prepare(state, out)
    return state, _read_artifacts(out)


def _scenario_repeated_ingest(ctx, state, out, batch_dir):
    ctx.ingest(state, batch_dir)
    ctx.ingest(state, batch_dir)
    ctx.rebuild(state)
    ctx.prepare(state, out)
    return state, _read_artifacts(out)


def _scenario_split_batch(ctx, state, out, a_dir, b_dir):
    ctx.ingest(state, a_dir)
    ctx.ingest(state, b_dir)
    ctx.rebuild(state)
    ctx.prepare(state, out)
    return state, _read_artifacts(out)


def _scenario_restart(ctx, state, out, a_dir, b_dir):
    ctx.ingest(state, a_dir)
    ctx.rebuild(state)
    state2 = state + ".resume"
    shutil.copy(state, state2)
    ctx.ingest(state2, b_dir)
    ctx.rebuild(state2)
    ctx.prepare(state2, out)
    return state2, _read_artifacts(out)


def _scenario_replay(ctx, state, out, batch_dir):
    ctx.ingest(state, batch_dir)
    ctx.rebuild(state)
    ctx.prepare(state, out, replay=True)
    return state, _read_artifacts(out)


def _scenario_revision_update(ctx, state, out, a_dir, b_dir):
    ctx.ingest(state, a_dir)
    ctx.rebuild(state)
    ctx.prepare(state, out)
    ctx.ingest(state, b_dir)
    ctx.rebuild(state)
    ctx.prepare(state, out)
    return state, _read_artifacts(out)


def _scenario_partitions(ctx, state, out, a_dir, b_dir):
    # [A][B], [A][empty][B], [A][B][dup B]
    ctx.ingest(state, a_dir)
    ctx.ingest(state, b_dir)
    ctx.ingest(state, b_dir)  # duplicate re-ingest of B
    ctx.rebuild(state)
    ctx.prepare(state, out)
    return state, _read_artifacts(out)


def _scenario_cross_source(ctx, state, out, sched_dir, adj_dir):
    # scheduling and adjustments arrive in different batches
    ctx.ingest(state, sched_dir)
    ctx.ingest(state, adj_dir)
    ctx.rebuild(state)
    ctx.prepare(state, out)
    return state, _read_artifacts(out)


@pytest.fixture(scope="module")
def stateful_ctx(tmp_path_factory):
    """Build the hidden stateful-universe inputs and the execution context.

    Oracle lifecycle: components run directly (trusted, /solution available).
    Agent lifecycle: the submitted /app program and the generated inputs are
    copied into a chroot jail; the components run inside it so /tests and
    /solution are not readable by the submitted program.
    """
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

    jail = _jail.Jail(str(tmp_path_factory.mktemp("jail")))
    ctx = _StatefulCtx(jail, str(base), out)
    if not ctx.oracle:
        jail.build()
        jail.install_program(os.path.join(os.sep, "app"))
        # The generated inputs are copied into the jail's /input; the
        # components are invoked with jail-relative paths pointing there.
        shutil.rmtree(jail.host("input"))
        shutil.copytree(base, jail.host("input"))
        jail._chmod_world_readable(jail.host("input"))
    return ctx


def _grade(state, out, produced, expected):
    for n in OUTPUT_NAMES:
        assert produced[n] == expected[n], (
            "stateful %s mismatch for %s" % (n, out))


@pytest.mark.parametrize("scenario", [
    "fresh", "repeated_ingest", "split_batch", "restart", "replay",
    "revision_update", "partitions", "cross_source",
])
@pytest.mark.parametrize("uname", hidden_universes.UNIVERSE_NAMES)
def test_stateful_convergence(scenario, uname, stateful_ctx, tmp_path):
    ctx = stateful_ctx
    u = ctx.universes[uname]
    expected_dir = u["full"]
    expected = hidden_reference._expected(expected_dir)

    if ctx.oracle:
        state = str(tmp_path / ("state_%s_%s.json" % (uname, scenario)))
        out = str(tmp_path / ("out_%s_%s" % (uname, scenario)))
        os.makedirs(out)
    else:
        state = ctx.jail.host("state/state_%s_%s.json" % (uname, scenario))
        out = ctx.jail.host("output/out_%s_%s" % (uname, scenario))
        ctx.jail.reset_mutable()
        os.makedirs(out)

    scn = {
        "fresh": lambda s, o: _fresh(ctx, s, o, u["full"]),
        "repeated_ingest": lambda s, o: _scenario_repeated_ingest(
            ctx, s, o, u["full"]),
        "split_batch": lambda s, o: _scenario_split_batch(
            ctx, s, o, u["a"], u["b"]),
        "restart": lambda s, o: _scenario_restart(ctx, s, o, u["a"], u["b"]),
        "replay": lambda s, o: _scenario_replay(ctx, s, o, u["full"]),
        "revision_update": lambda s, o: _scenario_revision_update(
            ctx, s, o, u["a"], u["b"]),
        "partitions": lambda s, o: _scenario_partitions(
            ctx, s, o, u["a"], u["b"]),
        "cross_source": lambda s, o: _scenario_cross_source(
            ctx, s, o, u["sched"], u["adj"]),
    }[scenario]

    produced_state, produced = scn(state, out)
    _grade(state, out, produced, expected)
    # Contract Section 30.3/30.5: whatever the execution history, the
    # persisted derived state must converge to a fresh recomputation over the
    # complete canonical ledger (observable at the state level, not only in
    # the final artifacts).
    _check_derived_state_converges(produced_state, scenario)
