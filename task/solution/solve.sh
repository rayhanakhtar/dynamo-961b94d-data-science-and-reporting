#!/bin/bash
#
# Entry point for the reference (oracle) solution.
#
# Harbor mounts solution/ at /solution/ and runs this script. The reference
# implementation is a genuine computation of the task; it reads the public
# input and writes the two required outputs to the absolute paths named in
# instruction.md (/app/output/...). No test or expected-output files are
# accessed.
set -euo pipefail
cd "$(dirname "$0")"
python3 solve.py
