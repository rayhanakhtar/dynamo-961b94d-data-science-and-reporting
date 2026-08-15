# Task

Prepare analysis-ready ambulatory room-capacity outputs from the supplied
operational scheduling sources, following the authoritative contract.

`/app/prepare_facts.py` is an existing implementation of the pipeline. It
runs, but it is known to produce incorrect results for some room-days,
bookings, and quality populations. Your job is to diagnose why it is wrong,
repair the implementation, and produce all five required artifacts correctly.

## Objective

Read every operational source under `/app/data/`, apply the contract's
semantic rules, identify where the existing implementation deviates from
them, repair `/app/prepare_facts.py`, and write the five required outputs
under `/app/output/`.

## Inputs

- `/app/data/scheduling_events.csv` — core reservation/revision feed (CSV).
- `/app/data/reservation_adjustments.ndjson` — adjustments/release feed
  (JSON Lines).
- `/app/data/clinic_directory.csv` — canonical clinic IDs and aliases.
- `/app/data/room_directory.csv` — clinic-scoped room IDs and aliases.
- `/app/data/docs/fact_contract.md` — the authoritative semantic contract.
- `/app/data/docs/fact_dictionary.toml` — structured schema/rule data that
  must agree with the contract.
- `/app/data/evidence/` — public diagnostic evidence: an incident report,
  a revision audit, and a partial capacity snapshot describing observed
  symptoms of the pipeline's incorrect results.

The two event feeds share one logical event schema. All sources together form
the complete event and identity universe.

## Required Outputs

Write exactly these five files:

1. `/app/output/effective_bookings.csv`
2. `/app/output/booking_state_history.csv`
3. `/app/output/daily_room_capacity_facts.csv`
4. `/app/output/capacity_reconciliation.csv`
5. `/app/output/quality_summary.json`

All five outputs are required and must conform exactly to the schemas,
populations, invariants, and sort orders in the contract. `start_ts`/`end_ts`
use minute-normalized timestamps; `created_ts`-derived history timestamps
preserve the validated source precision.

### quality_summary.json schema

`/app/output/quality_summary.json` is a single JSON object with exactly these
top-level categories, in this order:

`event_quality`, `identity_quality`, `revision_quality`,
`capacity_quality`, `reconciliation_quality`.

Each category is an object with exactly these keys, in this order:

```json
{
  "event_quality": {
    "total_raw_events": 0,
    "duplicate_events_removed": 0,
    "malformed_rows_rejected": 0,
    "adjustment_rows_valid": 0,
    "adjustment_rows_rejected": 0
  },
  "identity_quality": {
    "clinic_aliases_resolved": 0,
    "room_aliases_resolved": 0,
    "identity_tokens_unresolved": 0
  },
  "revision_quality": {
    "total_booking_ids": 0,
    "voided_revisions": 0,
    "bookings_with_effective_revision": 0,
    "active_bookings": 0,
    "cancelled_bookings": 0,
    "bookings_with_no_effective_revision": 0,
    "multi_day_active_bookings": 0
  },
  "capacity_quality": {
    "total_blocked_minutes": 0,
    "room_clinic_days_with_overlap": 0,
    "overlap_minutes_total": 0
  },
  "reconciliation_quality": {
    "reconciled_room_days": 0,
    "reconciliation_mismatches": 0
  }
}
```

All metric values are exact integers; there are no extra categories or keys;
JSON serialization is deterministic. The population and aggregation of every
metric are defined in contract Section 17.

## Evidence

The evidence under `/app/data/evidence/` describes observed symptoms of the
pipeline's incorrect results. It is advisory and partial: use it to form and
test hypotheses, but do not treat it as an authoritative specification or as
a substitute for the contract. Evidence is not a required output.

## Contract Authority

`/app/data/docs/fact_contract.md` is the authoritative specification for all
processing, schema, population, revision, identity, capacity,
reconciliation, and quality rules. Follow it for the complete semantics.

## Determinism and Completion

Produce all five required files in the specified schemas and ordering. Two
runs on the same inputs must produce identical outputs. Outputs must satisfy
the cross-artifact invariants defined by `fact_contract.md`.
