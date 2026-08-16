#!/usr/bin/env python3
"""Reference state-rebuild component.

Recomputes the derived canonical state (authoritative collision winners and
effective revisions) from the persisted canonical events. This is a
deterministic replay of the contract's collision and effective-revision
semantics (sections 5 and 7) over the persisted event ledger.

Usage:
  python3 bin/rebuild_state.py --state /app/state/state.json
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bin import state as state_mod  # noqa: E402


def rebuild(state_path):
    state = state_mod.load_state(state_path)
    ledger = state["canonical_events"]
    ledger.sort(key=lambda r: r["event_id"])

    # Authoritative collision winner per (booking_id, revision).
    authoritative = {}
    for rec in ledger:
        key = (rec["booking_id"], rec["revision"])
        prior = authoritative.get(key)
        if prior is None or (rec["created_ts"], rec["event_id"]) > \
                (prior["created_ts"], prior["event_id"]):
            authoritative[key] = rec

    # Effective revision: highest non-VOIDED authoritative winner.
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
