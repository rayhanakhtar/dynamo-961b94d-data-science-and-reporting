# Ambulatory Room-Capacity Facts

A data-preparation task: derive per-room daily capacity from an ambulatory
scheduling / exam-room reservation event export.

## Task objective

Repair `/app/prepare_facts.py` so it converts the event export into two
analysis-ready artifacts, exactly as specified by the authoritative business
contract at `/app/data/docs/fact_contract.md`.

## Input

`/app/data/scheduling_events.csv` — a synthetic UTF-8 CSV export of exam-room
reservation events: one header row followed by one record per line. Records
capture bookings, amendments, cancellations, voided revisions, re-bookings,
duplicate event rows, malformed rows, and overlapping reservations.

## Outputs

- `/app/output/daily_room_capacity_facts.csv` — one row per
  `(clinic_id, room_id, calendar_date)` with at least one blocked minute.
- `/app/output/quality_summary.json` — a twelve-key data-quality summary
  covering the pipeline's deduplication, validation, and revision accounting.

## Core processing requirements

- Field normalization, event de-duplication, and malformed-row handling.
- Revision resolution: a booking's effective revision is its highest-numbered
  non-`VOIDED` revision; `VOIDED` revisions are skipped wherever they appear,
  cancellations stand unless a later revision re-books the booking, and a
  booking with only `VOIDED` revisions contributes nothing.
- Intervals are half-open `[start, end)`, spread across calendar days, and
  aggregated into daily blocked minutes per room and clinic.

The central rule is that capacity is defined over one shared timeline per
`(clinic_id, room_id)` pair. All active bookings on the same room compete for
the same minutes: overlapping reservations contribute a union, never a sum, and
`provider_id` / `source` are descriptive attributes that never partition
capacity.

## Determinism

The pipeline is deterministic: fixed CSV sort order, fixed JSON key order, and
no timestamps or random values in the outputs.
