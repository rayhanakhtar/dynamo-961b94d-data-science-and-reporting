#!/usr/bin/env python3
"""Top-level entrypoint for the room-capacity pipeline.

Performs a fresh build (ingest -> rebuild -> prepare) from the operational
source inputs and writes the five required artifacts to the output directory.

Honors the DYNAMO_* environment overrides used by the grading harness:

  DYNAMO_INPUT_SCHEDULING / DYNAMO_INPUT_ADJUSTMENTS /
  DYNAMO_INPUT_CLINICS / DYNAMO_INPUT_ROOMS
  DYNAMO_EFFECTIVE_PATH / DYNAMO_HISTORY_PATH /
  DYNAMO_RECONCILIATION_PATH / DYNAMO_FACTS_PATH / DYNAMO_SUMMARY_PATH
  DYNAMO_STATE_PATH
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default=os.environ.get(
        "DYNAMO_STATE_PATH", "/app/state/state.json"))
    parser.add_argument("--output", default="/app/output")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.state), exist_ok=True)

    def run(script, extra):
        subprocess.check_call(
            [sys.executable, os.path.join(HERE, "bin", script)] + extra)

    run("ingest_events.py", ["--state", args.state])
    run("rebuild_state.py", ["--state", args.state])
    run("prepare_facts.py", ["--state", args.state, "--output", args.output])
    return 0


if __name__ == "__main__":
    sys.exit(main())
