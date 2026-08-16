#!/usr/bin/env python3
"""State rebuild component for the room-capacity pipeline.

Recomputes the derived canonical state (authoritative collision winners and
effective revisions) from the persisted canonical event ledger.

Usage:
  python3 bin/rebuild_state.py --state /app/state/state.json
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import state as state_mod


def _recompute_from_ledger(ledger):
    ledger.sort(key=lambda r: r["event_id"])
    authoritative = {}
    for rec in ledger:
        key = (rec["booking_id"], rec["revision"])
        prior = authoritative.get(key)
        if prior is None or (rec["created_ts"], rec["event_id"]) > \
                (prior["created_ts"], prior["event_id"]):
            authoritative[key] = rec
    by_booking = {}
    for rec in ledger:
        by_booking.setdefault(rec["booking_id"], set()).add(rec["revision"])
    effective = {}
    for booking_id, revisions in by_booking.items():
        chosen = None
        for revision in sorted(revisions, reverse=True):
            candidate = authoritative.get((booking_id, revision))
            if candidate is not None and candidate["status"] != "VOIDED":
                chosen = candidate
                break
        effective[booking_id] = chosen
    return authoritative, effective


def rebuild(state_path):
    state = state_mod.load_state(state_path)
    # Resume from a previously persisted checkpoint: if the last rebuild
    # completed, the derived state is assumed to already reflect the full
    # canonical ledger and recomputation is skipped.
    if state["checkpoint"].get("needs_rebuild") is False:
        state_mod.save_state(state_path, state)
        return {"authoritative": len(state["authoritative"]),
                "effective": len(state["effective"])}

    ledger = state["canonical_events"]
    authoritative, effective = _recompute_from_ledger(ledger)
    state["authoritative"] = {
        "%s|%s" % (k[0], k[1]): v["event_id"] for k, v in authoritative.items()
    }
    state["effective"] = {
        bid: (rec["event_id"] if rec is not None else None)
        for bid, rec in effective.items()
    }
    state["checkpoint"]["needs_rebuild"] = False
    state_mod.save_state(state_path, state)
    return {"authoritative": len(authoritative), "effective": len(effective)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default=state_mod.default_state_path())
    args = parser.parse_args()
    result = rebuild(args.state)
    print("authoritative=%d effective=%d" % (result["authoritative"],
                                             result["effective"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
