# Task

Repair `/app/prepare_facts.py` to convert the scheduling and reservation
sources into analysis-ready room-capacity outputs, exactly as specified by
the authoritative contract at `/app/data/docs/fact_contract.md`, which is the
sole specification of correct behavior; where anything else disagrees, the
contract wins.

`/app/prepare_facts.py` is the only file you must produce; inputs and contract
are fixed.

## Objective

Read every operational source under `/app/data/`, apply every contract rule,
and write the five required outputs under `/app/output/`.

## Inputs

- `/app/data/scheduling_events.csv` — core reservation/revision feed (CSV).
- `/app/data/reservation_adjustments.ndjson` — adjustments/release feed
  (JSON Lines).
- `/app/data/clinic_directory.csv` — canonical clinic IDs and aliases.
- `/app/data/room_directory.csv` — clinic-scoped room IDs and aliases.
- `/app/data/docs/fact_contract.md` — the authoritative semantic contract.
- `/app/data/docs/fact_dictionary.toml` — structured schema/rule data that
  must agree with the contract.

The two event feeds share one logical event schema. All sources together form
the complete event and identity universe.

## Required Outputs

Write exactly these five files:

1. `/app/output/effective_bookings.csv`
2. `/app/output/booking_state_history.csv`
3. `/app/output/daily_room_capacity_facts.csv`
4. `/app/output/capacity_reconciliation.csv`
5. `/app/output/quality_summary.json`

Each artifact's schema, population, sort order, invariants, and
cross-artifact consistency are defined in the contract.

### quality_summary.json schema

`/app/output/quality_summary.json` is a single UTF-8 JSON object with exactly
five top-level categories, in this order:

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

Each category is an object with the exact keys above, in the exact order
shown. Every value is an exact integer (no booleans, floats, `NaN`, or
`Infinity`); there are no extra categories or keys. The population, filtering
rule, and aggregation of every metric are defined in contract Section 17;
category order, key order, and integer constraints are fixed.

## Processing Requirements

1. **Source parsing and normalization.** Parse each feed (CSV and JSON
   Lines): trim fields, upper-case categorical tags, keep `event_id`/
   `booking_id` opaque, parse revision/date/time/timestamp truncating seconds
   to the minute, and normalize both feeds to one event shape, preserving
   source provenance.
2. **Deduplication.** Keep the first copy of each `event_id` across the
   combined event universe; discard later copies.
3. **Validation.** Reject rows failing the contract's malformed rules after
   deduplication; count them in the quality report.
4. **Clinic identity resolution.** Resolve each clinic token via the clinic
   directory to a canonical clinic ID, falling back to the normalized token;
   clinic resolution precedes room resolution.
5. **Room identity resolution.** Resolve each room token via the room
   directory to a canonical room ID scoped by its canonical clinic, falling
   back to the normalized token; the same alias may map to different rooms in
   different clinics.
6. **Cross-source collision.** For every `(booking_id, revision)`, select one
   authoritative row: latest `created_ts`, ties broken by greatest `event_id`
   (as a string), from all validated rows across all sources. VOIDED rows
   participate in collision resolution.
7. **Effective revision.** For each booking, select the highest-numbered
   authoritative revision whose status is not VOIDED; if all are VOIDED, the
   booking has no effective revision.
8. **Effective bookings snapshot.** Emit one row per booking with an effective
   revision, using only that revision's status, canonical room/clinic,
   interval, provider, and source.
9. **Booking state history.** Emit one row per authoritative revision with
   deterministic `effective_from`/`effective_to` and an `is_effective` flag
   marking exactly the current effective revision when one exists.
10. **Active state and materialization.** Only effective BOOKED bookings
    contribute capacity. Intervals are half-open `[start, end)`; a minute
    belongs to the day it starts in; zero-length intervals block nothing.
11. **Shared room/clinic union.** All active bookings on the same
    `(clinic_id, room_id)` share one timeline; blocked minutes are the union,
    never the sum. `provider_id` and `source` never partition capacity.
12. **Daily facts.** For each room/clinic/day with at least one blocked
    minute, emit `blocked_minutes`.
13. **Capacity reconciliation.** For each room/clinic/day, report effective
    booking count, raw interval minutes, blocked minutes, and overlap
    minutes, satisfying `raw = blocked + overlap`.
14. **Quality summary.** Produce the deterministic categorized
    `quality_summary.json` per the schema above.

## Determinism Requirements

Two runs on the same inputs must produce identical outputs: identical CSV
rows (values and order) and an identical JSON object (keys, order, values).
Sort and JSON key orders are fixed by the contract; internal ordering must
not affect output.

## Completion Criteria

The task is complete when `/app/prepare_facts.py` runs successfully and
deterministically produces the five required outputs conforming exactly to
the contract's schemas, populations, invariants, and sort orders.
