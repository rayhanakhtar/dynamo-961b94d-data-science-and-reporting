"""Shared persisted-state helpers for the stateful room-capacity pipeline.

State format (single JSON document):

{
  "schema_version": 1,
  "sources": { "<absolute source dir>": {"total_raw": N, "adj_total": N,
              "duplicates": N, "malformed": N} },
  "ingested_event_ids": ["EV-...", ...],
  "canonical_events": [ {ledger record with clinic_hit/room_hit}, ... ],
  "authoritative": { "<booking>|<revision>": "<event_id>" },
  "effective": { "<booking>": "<event_id>" },
  "checkpoint": { "needs_rebuild": true }
}

The canonical_events are the validated, deduplicated, identity-resolved event
ledger. authoritative/effective are derived views recomputed by rebuild_state.
Everything needed to materialize the five output artifacts is persisted here.
"""

import json
import os

SCHEMA_VERSION = 1
STATE_ENV = "DYNAMO_STATE_PATH"


def default_state_path():
    return os.environ.get(STATE_ENV, "/app/state/state.json")


def load_state(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as h:
            return json.load(h)
    return {
        "schema_version": SCHEMA_VERSION,
        "sources": {},
        "ingested_event_ids": [],
        "canonical_events": [],
        "authoritative": {},
        "effective": {},
        "checkpoint": {"needs_rebuild": True},
    }


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as h:
        json.dump(state, h, indent=2, sort_keys=True)
        h.write("\n")
    os.replace(tmp, path)


def new_state(path):
    """Start a fresh empty state (used by the top-level fresh-build entry)."""
    save_state(path, {
        "schema_version": SCHEMA_VERSION,
        "sources": {},
        "ingested_event_ids": [],
        "canonical_events": [],
        "authoritative": {},
        "effective": {},
        "checkpoint": {"needs_rebuild": True},
    })
    return load_state(path)
