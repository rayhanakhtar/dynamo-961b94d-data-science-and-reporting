"""Shared persisted-state helpers for the room-capacity pipeline.

State is a single JSON document (see README / contract section 30):

{
  "schema_version": 1,
  "sources": { "<source key>": {"total_raw": N, "adj_total": N,
              "duplicates": N, "malformed": N} },
  "ingested_event_ids": ["EV-...", ...],
  "canonical_events": [ {...}, ... ],
  "authoritative": { "<booking>|<revision>": "<event_id>" },
  "effective": { "<booking>": "<event_id>" },
  "checkpoint": { "needs_rebuild": true }
}
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
