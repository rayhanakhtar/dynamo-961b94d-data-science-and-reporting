# Task

Prepare analysis-ready ambulatory room-capacity outputs from the supplied
operational scheduling sources, following the authoritative contract.

The system under `/app` is a stateful pipeline that maintains persisted
canonical state and produces the required artifacts. It runs, but it is known
to be incorrect under some operational execution modes. Your job is to
diagnose why, repair the implementation, and produce all five required
artifacts correctly and consistently across execution modes.

## Objective

Read every operational source under `/app/data/`, apply the contract's
semantic rules, identify where the existing implementation deviates from
them, repair it, and write the five required outputs under `/app/output/`.

The pipeline has three logical components and a persisted state:

- `/app/bin/ingest_events.py` — ingests operational events into persisted
  canonical state;
- `/app/bin/rebuild_state.py` — rebuilds derived canonical state from the
  persisted ledger;
- `/app/bin/prepare_facts.py` — materializes the five output artifacts;
- `/app/state/` — persisted canonical state;
- `/app/prepare_facts.py` — top-level dispatcher that performs a fresh build.

The persisted pipeline state is maintained at `/state/state.json` and must
remain consistent across ingestion, rebuild, restart, and replay operations.

The system must be correct across fresh builds, incremental ingestion,
persisted state, replay, restart, and idempotent reprocessing: for any valid
execution history of the same logical event universe, the five final
artifacts must be identical (contract Section 30).

## Inputs

- `/app/data/scheduling_events.csv` — core reservation/revision feed (CSV).
- `/app/data/reservation_adjustments.ndjson` — adjustments/release feed
  (JSON Lines).
- `/app/data/clinic_directory.csv` — canonical clinic IDs and aliases.
- `/app/data/room_directory.csv` — clinic-scoped room IDs and aliases.
- `/app/data/docs/fact_contract.md` — the authoritative semantic contract.
- `/app/data/docs/fact_dictionary.toml` — structured schema/rule data that
  must agree with the contract.
- `/app/data/evidence/` — public diagnostic evidence describing observed
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
reconciliation, quality, and operational-execution rules. Follow it for the
complete semantics.

## Determinism and Completion

Produce all five required files in the specified schemas and ordering. Two
runs on the same inputs must produce identical outputs. Outputs must satisfy
the cross-artifact invariants defined by `fact_contract.md`, and the
operational invariants of contract Section 30: fresh build, incremental
ingestion, replay from persisted state, restart from a checkpoint, and
idempotent reprocessing of the same logical event universe must all produce
identical final artifacts.
