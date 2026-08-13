#!/usr/bin/env python3
"""
Reference oracle for the multi-source room-capacity task.

Orchestrates the modular pipeline:

  parse -> normalize -> resolve -> history -> materialize -> reconcile
  -> quality

It reads only the public task inputs and the authoritative contract semantics,
and writes the deterministic output artifacts. It never reads tests/,
expected/, verifier code, or agent outputs, and never imports or executes the
starter implementation.
"""

import csv
import json
import os
import sys

import parse
import normalize
import resolve
import history
import materialize
import reconcile
import quality

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


def write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row[col] for col in columns])


def write_facts(path, fact_rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(FACT_COLUMNS)
        for room_id, clinic_id, day, minutes in fact_rows:
            writer.writerow([room_id, clinic_id, day.isoformat(), minutes])


def main():
    missing = [p for p in (INPUT_SCHEDULING, INPUT_ADJUSTMENTS, INPUT_CLINICS,
                           INPUT_ROOMS) if not os.path.exists(p)]
    if missing:
        sys.stderr.write("input(s) not found: %s\n" % ", ".join(missing))
        return 2

    # Stage 1: source parsing.
    raw_sched = parse.read_scheduling_events(INPUT_SCHEDULING)
    raw_adj = parse.read_reservation_adjustments(INPUT_ADJUSTMENTS)
    clinic_map = parse.read_clinic_directory(INPUT_CLINICS)
    room_map = parse.read_room_directory(INPUT_ROOMS)

    # Combined event universe: core feed first, then adjustments.
    combined = raw_sched + raw_adj
    total_raw = len(combined)

    # Stage 2: dedup then validate.
    surviving, duplicates = normalize.deduplicate(combined)
    records = []
    malformed = 0
    for row in surviving:
        rec = normalize.normalize_event(row)
        if rec is None:
            malformed += 1
        else:
            records.append(rec)

    # Stage 3-4-5: identity resolution and canonical ledger.
    ledger, identity_stats = resolve.build_ledger(records, clinic_map, room_map)

    # Stage 6: cross-source collision resolution.
    authoritative = resolve.resolve_collisions(ledger)

    # Stage 7: effective revision.
    effective = resolve.select_effective(ledger, authoritative)

    # Stage 8: effective bookings + history.
    effective_rows = history.build_effective_rows(effective)
    history_rows = history.build_history_rows(ledger, authoritative, effective)

    # Materialization + reconciliation + quality.
    fact_rows = materialize.build_facts(effective)
    reconciliation_rows = reconcile.build_reconciliation(effective)
    summary = quality.build_categorical_summary(
        total_raw, duplicates, malformed, records, effective, fact_rows,
        reconciliation_rows, identity_stats, len(raw_adj))

    write_csv(LEDGER_PATH, LEDGER_COLUMNS, ledger)
    write_csv(EFFECTIVE_PATH, EFFECTIVE_COLUMNS, effective_rows)
    write_csv(HISTORY_PATH, HISTORY_COLUMNS, history_rows)
    write_csv(RECONCILIATION_PATH, RECONCILIATION_COLUMNS,
              reconciliation_rows)
    write_facts(FACTS_PATH, fact_rows)
    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)
    with open(SUMMARY_PATH, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
