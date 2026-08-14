#!/usr/bin/env python3
"""
Daily room capacity facts.

Reads the operational event sources for an ambulatory scheduling /
exam-room reservation system and produces analysis-ready room-capacity
artifacts under /app/output.

The authoritative specification of all processing and output semantics is
/app/data/docs/fact_contract.md; a structured schema/rule mirror lives in
/app/data/docs/fact_dictionary.toml.

This module provides the I/O infrastructure: path wiring, source readers,
generic serialization helpers, and a main entry point. The transformation
from the loaded sources into the required output artifacts must be
implemented from the contract.

Inputs:
  /app/data/scheduling_events.csv
  /app/data/reservation_adjustments.ndjson
  /app/data/clinic_directory.csv
  /app/data/room_directory.csv
  /app/data/docs/fact_contract.md
  /app/data/docs/fact_dictionary.toml

Outputs:
  /app/work/canonical_events.csv
  /app/output/effective_bookings.csv
  /app/output/booking_state_history.csv
  /app/output/daily_room_capacity_facts.csv
  /app/output/capacity_reconciliation.csv
  /app/output/quality_summary.json
"""

import csv
import json
import os
import sys

INPUT_SCHEDULING = os.environ.get("DYNAMO_INPUT_SCHEDULING",
                                  "/app/data/scheduling_events.csv")
INPUT_ADJUSTMENTS = os.environ.get("DYNAMO_INPUT_ADJUSTMENTS",
                                   "/app/data/reservation_adjustments.ndjson")
INPUT_CLINICS = os.environ.get("DYNAMO_INPUT_CLINICS",
                               "/app/data/clinic_directory.csv")
INPUT_ROOMS = os.environ.get("DYNAMO_INPUT_ROOMS",
                             "/app/data/room_directory.csv")

LEDGER_PATH = os.environ.get("DYNAMO_LEDGER_PATH",
                             "/app/work/canonical_events.csv")
EFFECTIVE_PATH = os.environ.get("DYNAMO_EFFECTIVE_PATH",
                                "/app/output/effective_bookings.csv")
HISTORY_PATH = os.environ.get("DYNAMO_HISTORY_PATH",
                              "/app/output/booking_state_history.csv")
RECONCILIATION_PATH = os.environ.get("DYNAMO_RECONCILIATION_PATH",
                                     "/app/output/capacity_reconciliation.csv")
FACTS_PATH = os.environ.get("DYNAMO_FACTS_PATH",
                            "/app/output/daily_room_capacity_facts.csv")
SUMMARY_PATH = os.environ.get("DYNAMO_SUMMARY_PATH",
                              "/app/output/quality_summary.json")

LEDGER_COLUMNS = ["event_id", "booking_id", "revision", "status", "clinic_id",
                  "room_id", "provider_id", "source", "start_ts", "end_ts",
                  "created_ts"]
EFFECTIVE_COLUMNS = ["booking_id", "status", "clinic_id", "room_id",
                     "start_ts", "end_ts", "provider_id", "source",
                     "effective_revision"]
HISTORY_COLUMNS = ["booking_id", "revision", "status", "clinic_id", "room_id",
                   "start_ts", "end_ts", "effective_from", "effective_to",
                   "is_effective"]
RECONCILIATION_COLUMNS = ["room_id", "clinic_id", "calendar_date",
                          "effective_booking_count", "raw_interval_minutes",
                          "blocked_minutes", "overlap_minutes"]
FACT_COLUMNS = ["room_id", "clinic_id", "calendar_date", "blocked_minutes"]


# ---------------------------------------------------------------------------
# Source readers
# ---------------------------------------------------------------------------

def read_csv_rows(path):
    """Read a delimited file with a header row into a list of row dicts."""
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json_lines(path):
    """Read a newline-delimited JSON file into a list of objects."""
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def read_clinic_directory(path):
    """Read the clinic directory into a lookup table."""
    mapping = {}
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            mapping[row["clinic_alias"].strip().upper()] = \
                row["canonical_clinic_id"].strip().upper()
    return mapping


def read_room_directory(path):
    """Read the room directory into a lookup table."""
    mapping = {}
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            mapping[(row["clinic_id"].strip().upper(),
                     row["room_alias"].strip().upper())] = \
                row["canonical_room_id"].strip().upper()
    return mapping


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row[col] for col in columns])


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2)
        handle.write("\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_inputs():
    """Read all source artifacts and return the loaded data."""
    scheduling = read_csv_rows(INPUT_SCHEDULING)
    adjustments = read_json_lines(INPUT_ADJUSTMENTS)
    clinics = read_clinic_directory(INPUT_CLINICS)
    rooms = read_room_directory(INPUT_ROOMS)
    return scheduling, adjustments, clinics, rooms


def process(scheduling, adjustments, clinics, rooms):
    """Transform loaded source data into the required output structures.

    The full transformation semantics are defined by fact_contract.md. This
    scaffold returns placeholder structures; implement the transformation
    from the contract and return the data that write_outputs serializes.
    """
    return {
        "ledger": [],
        "effective_bookings": [],
        "history": [],
        "facts": [],
        "reconciliation": [],
        "quality": {},
    }


def write_outputs(result):
    """Serialize the produced structures to the required output paths."""
    write_csv(LEDGER_PATH, LEDGER_COLUMNS, result["ledger"])
    write_csv(EFFECTIVE_PATH, EFFECTIVE_COLUMNS, result["effective_bookings"])
    write_csv(HISTORY_PATH, HISTORY_COLUMNS, result["history"])
    write_csv(RECONCILIATION_PATH, RECONCILIATION_COLUMNS,
              result["reconciliation"])
    write_csv(FACTS_PATH, FACT_COLUMNS, result["facts"])
    write_json(SUMMARY_PATH, result["quality"])


def main():
    missing = [p for p in (INPUT_SCHEDULING, INPUT_ADJUSTMENTS, INPUT_CLINICS,
                           INPUT_ROOMS) if not os.path.exists(p)]
    if missing:
        sys.stderr.write("input(s) not found: %s\n" % ", ".join(missing))
        return 2

    data = load_inputs()
    result = process(*data)
    write_outputs(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
