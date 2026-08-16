#!/bin/bash
#
# Entry point for the reference (oracle) solution.
#
# Harbor mounts solution/ at /solution/ and runs this script. The reference
# implementation is a genuine stateful computation of the task: it performs a
# fresh build (ingest -> rebuild -> prepare) from the operational source
# inputs and writes the five required artifacts to /app/output (or the paths
# named by the DYNAMO_*_PATH environment variables). No test or
# expected-output files are accessed.
set -euo pipefail
cd "$(dirname "$0")"

STATE_DIR="$(mktemp -d)"
STATE_PATH="$STATE_DIR/state.json"
trap 'rm -rf "$STATE_DIR"' EXIT

export DYNAMO_STATE_PATH="$STATE_PATH"

python3 bin/ingest_events.py
python3 bin/rebuild_state.py
python3 bin/prepare_facts.py
