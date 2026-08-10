# Task

Repair `/app/prepare_facts.py` to convert the ambulatory scheduling event export into a daily room-capacity fact table and a data-quality summary, exactly as specified by the authoritative contract at `/app/data/docs/fact_contract.md`. It is the sole specification of correct behavior; where anything else disagrees, the contract wins.

`/app/prepare_facts.py` is the only file you must produce; the input and contract are fixed. The Python standard library is sufficient. Run it whenever useful (e.g. `python3 /app/prepare_facts.py`) to inspect its current behavior.

## Objective

Read `/app/data/scheduling_events.csv`, apply every rule in the contract, and write:

- `/app/output/daily_room_capacity_facts.csv`
- `/app/output/quality_summary.json`

## Input

`/app/data/scheduling_events.csv` is a synthetic export of exam-room reservation events: UTF-8 CSV, one header row then one record per line, with exactly the 13 columns of the contract's input schema (`event_id`, `booking_id`, `revision`, `status`, `clinic_id`, `room_id`, `provider_id`, `source`, `start_date`, `start_time`, `end_date`, `end_time`, `created_ts`). It deliberately contains the real-world states the contract describes.

## Required Outputs

Exactly two files:

1. `/app/output/daily_room_capacity_facts.csv` — one row per `(clinic_id, room_id, calendar date)` with at least one blocked minute.
2. `/app/output/quality_summary.json` — the twelve-integer quality summary documented in the contract.

## Processing Requirements

Each rule is specified in full in the contract; apply them exactly.

### 1. Normalize and validate input
Trim leading and trailing whitespace from every field. `clinic_id`, `room_id`, `provider_id`, `source`, and `status` are categorical tags compared case-insensitively and output in canonical upper case; `event_id` and `booking_id` are trimmed but compared literally. `revision` is a non-negative whole number. Dates are `YYYY-MM-DD`; times are `HH:MM` or `HH:MM:SS`, with seconds truncated to the minute; all times are local wall-clock with no time-zone conversion. Rows failing the contract's malformed-row rules are excluded from all capacity computation and counted in `malformed_rows_rejected`; malformed detection applies to the rows that survive deduplication, and rejecting a row never invalidates the rest of its booking.

### 2. Deduplicate events
A raw event is identified by `event_id`. When the export repeats an `event_id`, keep the first copy in file order and discard later copies; each counts toward `duplicate_events_removed`.

### 3. Resolve booking revisions
A logical booking is identified by its `booking_id`; every row carrying a `booking_id` and a `revision` is a revision of that booking. If two or more rows share the same `(booking_id, revision)`, the row with the latest `created_ts` is authoritative, with the greatest `event_id` (compared as a string) breaking ties. A booking's effective revision is found by scanning its revisions from highest to lowest and skipping every `voided` revision; the first revision whose status is `booked` or `cancelled` governs the booking. If every revision is `voided`, the booking has no effective revision and contributes nothing. A `voided` revision is skipped wherever it appears, even when it is the highest-numbered revision; a `cancelled` effective revision stands unless a later revision changes the booking's state.

### 4. Determine active bookings
A booking is active if and only if its effective revision's status is `booked`. Cancelled bookings, bookings with no effective revision, and voided revisions never contribute capacity.

### 5. Materialize blocked intervals
An effective revision's interval is half-open `[start, end)`: the start instant is occupied, the end instant is not. Times are normalized to the minute. A zero-length interval (`end` equal to `start`) blocks nothing; an interval whose end is earlier than its start is malformed. A minute belongs to the calendar day in which it starts; multi-day intervals spread across days, and a day with zero blocked minutes contributes nothing.

### 6. Resolve shared room-clinic capacity
This is the central rule. Capacity is calculated independently for each `(clinic_id, room_id)` pair; the same `room_id` in different clinics is a different room. All active bookings on the same pair share one room timeline; blocked minutes are the union of the overlapping intervals, never their sum. `provider_id` and `source` are descriptive attributes only: they do not partition the capacity calculation and never appear in the output. Intervals that merely touch do not overlap; each minute is counted once.

### 7. Aggregate daily blocked minutes
For each `(clinic_id, room_id, calendar date)`, `blocked_minutes` is the number of minutes on that day blocked by at least one active booking on that room. The fact table contains a row only for combinations with `blocked_minutes >= 1`.

### 8. Produce the quality summary
`quality_summary.json` must contain exactly the twelve integer keys defined by the contract, in the contract's order, with no extra keys. Every value is an exact integer — no rounding is applied anywhere in the pipeline. `total_blocked_minutes` must equal the sum of `blocked_minutes` over every row of the fact table, and the two consistency identities documented in the contract must hold.

## Output Schema

`daily_room_capacity_facts.csv`: UTF-8, no byte-order mark, a header row, one record per line. Columns in exact order: `room_id`, `clinic_id`, `calendar_date`, `blocked_minutes`. `room_id` and `clinic_id` in canonical upper case, `calendar_date` as `YYYY-MM-DD`, and `blocked_minutes` must be a positive integer for every emitted row. Rows sorted ascending by `room_id`, then `clinic_id`, then `calendar_date`.

`quality_summary.json`: a single JSON object with exactly the twelve keys documented in the contract, in the contract's order; every value an integer.

## Determinism Requirements

Two runs on the same input must produce identical outputs: identical fact-table rows (values and order) and an identical JSON object (key set, key order, and values). The CSV sort order and the JSON key order are fixed by the contract; internal ordering choices must not affect the output.

## Completion Criteria

The task is complete when `/app/prepare_facts.py` satisfies the contract, runs successfully, and deterministically produces the two required files conforming exactly to the schemas and rules above.
