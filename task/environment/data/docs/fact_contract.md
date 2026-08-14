# Fact Contract — Daily Room Capacity Facts

This document is the authoritative specification for converting ambulatory
scheduling / exam-room reservation operational information into a daily
room-capacity fact table, an effective-booking snapshot, a booking-state
history, a capacity reconciliation, and a categorized data-quality summary.
A correct implementation must satisfy every rule in this document. When this
contract and any other description disagree, this contract wins.

Many rules in this contract are defined once and then consumed by other
sections. Section 3.1 is the single canonical terminology section: every
population and state term used below has its definition there. Sections 4, 5,
7, 8, 16, and 17 each define one stage of a population lineage; sections 9-14
and 26 define temporal and capacity semantics; sections 20-22 define identity;
and sections 23-27 define the output artifacts and their invariants. Where a
section states that a definition "is consumed by" or "feeds" another section,
the two sections must be read together to obtain the complete rule.

## 1. Inputs

The task consumes four source artifacts plus this contract and the structured
dictionary. All sources together constitute one operational event and identity
universe; a correct implementation must consume every source.

| Path | Contents |
|------|----------|
| `/app/data/scheduling_events.csv` | The core reservation/revision feed (UTF-8 CSV, one header row, then one record per line). |
| `/app/data/reservation_adjustments.ndjson` | Late adjustment/correction/release feed (JSON Lines). |
| `/app/data/clinic_directory.csv` | Canonical clinic IDs and aliases. |
| `/app/data/room_directory.csv` | Clinic-scoped canonical room IDs and aliases. |
| `/app/data/docs/fact_contract.md` | This contract (authoritative semantics). |
| `/app/data/docs/fact_dictionary.toml` | Structured machine-readable schema/rule data (must agree with this contract). |

## 2. Input schemas

### 2.1 scheduling_events.csv

The core feed has exactly these 13 columns, in this order:

| Column | Description | Example |
|--------|-------------|---------|
| `event_id` | Identifier of a single raw event row | `EV-0000042` |
| `booking_id` | Identifier of the logical booking the row describes | `BK-0421` |
| `revision` | Revision number of the booking as a whole number | `2` |
| `status` | One of `booked`, `cancelled`, `voided` | `booked` |
| `clinic_id` | Clinic the room belongs to | `CL-A` |
| `room_id` | Exam room, unique only within a clinic | `RM-01` |
| `provider_id` | Provider associated with the booking (attribute) | `P003` |
| `source` | Booking source tag (attribute) | `PORTAL` |
| `start_date` | Start date of the booked interval, `YYYY-MM-DD` | `2026-01-06` |
| `start_time` | Start time of the booked interval, `HH:MM` or `HH:MM:SS` | `09:00` |
| `end_date` | End date of the booked interval, `YYYY-MM-DD` | `2026-01-06` |
| `end_time` | End time of the booked interval, `HH:MM` or `HH:MM:SS` | `12:00` |
| `created_ts` | When the row was recorded, `YYYY-MM-DD HH:MM:SS` | `2026-01-05 08:15:00` |

### 2.2 reservation_adjustments.ndjson

A JSON Lines file: one UTF-8 JSON object per line, no header row. Each object
carries the same logical event fields as Section 2.1 and is subject to the same
normalization and malformed-input rules (Sections 3 and 16). The `source` field
of every adjustment row identifies this feed (for example `ADJUSTMENT`).

Required object keys and types:

| Key | Type | Example |
|-----|------|---------|
| `event_id` | string | `EV-0005043` |
| `booking_id` | string | `BK-5201` |
| `revision` | integer | `2` |
| `status` | string | `cancelled` |
| `clinic_id` | string | `CL-A` |
| `room_id` | string | `RM-15` |
| `provider_id` | string | `P077` |
| `source` | string | `ADJUSTMENT` |
| `start_date` | string (`YYYY-MM-DD`) | `2026-04-28` |
| `start_time` | string (`HH:MM` or `HH:MM:SS`) | `09:00` |
| `end_date` | string (`YYYY-MM-DD`) | `2026-04-28` |
| `end_time` | string (`HH:MM` or `HH:MM:SS`) | `10:00` |
| `created_ts` | string (`YYYY-MM-DD HH:MM:SS`) | `2026-04-26 11:00:00` |

A row is malformed (Section 16) if any required key is missing or unparsable.

### 2.3 clinic_directory.csv

UTF-8 CSV, one header row, then one record per line. Columns:

| Column | Description |
|--------|-------------|
| `canonical_clinic_id` | Canonical clinic code. |
| `clinic_alias` | A token that maps to the canonical clinic. |

### 2.4 room_directory.csv

UTF-8 CSV, one header row, then one record per line. Columns:

| Column | Description |
|--------|-------------|
| `clinic_id` | Canonical clinic code (the room scope). |
| `room_alias` | A room token that maps to the canonical room. |
| `canonical_room_id` | Canonical room code within that clinic. |

Both directories use the field-normalization rules of Section 3.

## 3. Field normalization

- Trim leading and trailing whitespace from every field before any comparison or
  output.
- `clinic_id`, `room_id`, `provider_id`, `source`, and `status` are categorical
  tags: they are compared case-insensitively and their canonical form is the
  trimmed value in upper case. All output uses the canonical form.
- `event_id` and `booking_id` are opaque identifiers: they are trimmed but are
  case-sensitive and otherwise compared literally.
- `revision` is a non-negative whole number (for example `2`, not `2.0`).
- Dates are `YYYY-MM-DD`. Times are `HH:MM` or `HH:MM:SS`; seconds, if present,
  are truncated (floored) to the minute.
- All times are local wall-clock time. No time-zone conversion is ever applied.
- In every output artifact, timestamps are serialized as strings in the exact
  format `YYYY-MM-DD HH:MM:SS`. Because input times are truncated to the
  minute, normalized output timestamps always have a seconds component equal
  to `00`. This applies to `start_ts`, `end_ts`, `created_ts`, and every
  derived timestamp field in the output artifacts. The canonical instants of
  Section 23 and the effective/history instants of Sections 24-25 all use this
  serialization.

### 3.1 Terminology and population vocabulary

The following terms have fixed meanings throughout this contract. Every other
section that uses one of these terms inherits exactly this meaning and no
other. The population lineage in Section 17 chains these terms together;
Sections 4, 5, 7, 8, 16, and 24-26 are the consumers of the definitions below.

1. **Deduplication survivor** - a row retained by the `event_id`
   first-occurrence rule of Section 4. Rows discarded as duplicate copies are
   not deduplication survivors. Section 4 defines the rule; Section 16 defines
   which of these rows are subsequently rejected as malformed.

2. **Validated surviving row** - a deduplication survivor that passes the
   malformed-input rules of Section 16. The validated surviving row population
   is the population from which every quality-summary metric is derived (see
   the population lineage of Section 17 and the `identity_quality`/`revision_quality`
   definitions there). Later pipeline stages never remove rows from it:
   collision resolution (Section 5), effective-state selection (Section 7), and
   capacity materialization (Sections 10-14) refine or select from it but never
   delete from it.

3. **Authoritative collision winner** - for a given `(booking_id, revision)`,
   the single validated surviving row selected by the collision rule of
   Section 5 (latest `created_ts`, ties broken by greatest `event_id` compared
   as strings). It is the row whose content (status, clinic, room, interval,
   provider, source) represents that revision for downstream state selection.
   Collision losers do NOT disappear from the validated surviving row
   population: every row that loses a collision remains an eligible validated
   surviving row for every quality-summary metric whose population is the
   validated surviving rows (Section 17). The authoritative collision winner is
   the population consumed by effective-revision selection (Section 7), by the
   booking-state history (Section 25), and by the effective-booking snapshot
   (Section 24).

4. **Effective revision** - the highest-numbered authoritative collision winner
   whose status is not `voided` (Section 7). A booking whose every authoritative
   collision winner is `voided` has no effective revision. The effective
   revision is the state consumed by the active-booking rule (Section 8), by
   the effective-booking snapshot (Section 24), and by capacity computation
   (Sections 10-14 and 26). A booking's effective revision is selected by
   revision number, not by `created_ts` chronology (Section 7).

5. **Effective BOOKED booking** - a booking whose effective revision (term 4)
   has status `booked` (Section 8). This is the population that may contribute
   capacity (Sections 10-14); it is distinct from the set of bookings that
   actually contribute at least one blocked minute on a given room-day
   (term 6) and from the set of reconciliation room-days (term 7).

6. **Positive-minute capacity contributor** - an effective BOOKED booking
   (term 5) that contributes at least one blocked minute on a given
   `(clinic_id, room_id, calendar_date)` (Sections 10, 12, 14). A zero-length
   effective BOOKED booking contributes zero minutes and is therefore not a
   positive-minute capacity contributor on any day.

7. **Reconciliation room-day** - a `(room_id, clinic_id, calendar_date)`
   combination that has at least one positive-minute capacity contributor
   (term 6). Section 26 defines the reconciliation artifact over this
   population. A room-day that has no positive-minute capacity contributor has
   no reconciliation row.

## 4. Event identity and duplicate events

- A raw event is identified by its `event_id`.
- Any event source may contain the same raw event more than once (an artifact
  of how the operational system was assembled). Deduplication is applied to
  the union of all event rows across all event sources: `event_id` values are
  globally unique across `scheduling_events.csv` and
  `reservation_adjustments.ndjson`. For each `event_id`, only the first copy in
  source/file order is kept; every later copy of the same `event_id` is a
  duplicate and is discarded. Each discarded copy counts toward
  `duplicate_events_removed` (Section 17). A row kept by this deduplication is
  a **deduplication survivor** (Section 3.1); a discarded copy is not.
- The deduplication survivor population (term 1 of Section 3.1) is the input
  to malformed-input rejection (Section 16). Deduplication therefore precedes
  validation: a later copy of a duplicated `event_id` is discarded and is
  never validated, and it never participates in collision resolution
  (Section 5).

## 5. Booking identity and collision

- A logical booking is identified by its `booking_id`. Every row that carries a
  `booking_id` and a `revision` is one *revision event* of that booking.
- Revisions of one booking are ordered by their integer `revision` value.
  Revision numbers need not be contiguous.
- The collision population is the set of validated surviving rows (term 2 of
  Section 3.1) across **all** event sources (Section 4). If two or more of
  those rows carry the same `(booking_id, revision)` pair, the row with the
  latest `created_ts` is the **authoritative collision winner** (term 3 of
  Section 3.1) for that revision; if `created_ts` ties, the row with the
  greatest `event_id` (compared as strings) wins. A `scheduling_events.csv`
  row and a `reservation_adjustments.ndjson` row for the same
  `(booking_id, revision)` compete under the same rule.
- The authoritative collision winner supplies the revision's content for
  effective-revision selection (Section 7), for the booking-state history
  (Section 25), and for the effective-booking snapshot (Section 24). Collision
  resolution does not remove the losing rows from the validated surviving row
  population (term 2 of Section 3.1); those losing rows remain eligible
  validated surviving rows for the quality-summary counts of Section 17.

## 6. Status semantics

Each revision event has exactly one status, interpreted by the sections that
consume it:

- `booked` — the booking occupies the room for the interval in that row.
  A `booked` status on an authoritative collision winner (term 3 of
  Section 3.1) is what makes a booking active (Section 8) and able to
  contribute capacity (Sections 10-14, 26).
- `cancelled` — as of this revision the booking no longer occupies the room.
  An effective revision (term 4 of Section 3.1) with status `cancelled`
  yields a non-active booking (Section 8) that contributes no capacity.
- `voided` — this revision was recorded in error and is annulled. A voided
  revision is treated as though it never existed: it does not change the
  booking's state and contributes no interval. A VOIDED row still participates
  in collision resolution (Section 5), may still be the authoritative
  collision winner for its `(booking_id, revision)`, and is still represented
  in the booking-state history (Section 25); but a VOIDED authoritative
  collision winner can never be a booking's effective revision (Section 7),
  never makes a booking active (Section 8), and never contributes capacity.

## 7. Effective revision

- A booking's **effective revision** is the highest-numbered authoritative
  collision winner (term 3 of Section 3.1, selected per Section 5) whose
  status is not `voided` (Section 6). The selection is by revision number, not
  by `created_ts`: a lower-numbered revision may be effective even when a
  higher-numbered revision was recorded later. A `voided` authoritative
  collision winner never provides the booking's final status, room, clinic, or
  interval, wherever it appears in the revision order.
- The booking's final state and interval come only from its effective revision.
- The effective revision's row also fixes the booking's `clinic_id` and
  `room_id`: the booking's blocked minutes are attributed to the effective
  revision's `(clinic_id, room_id)`, never to a superseded revision's room or
  clinic. The canonical clinic/room of the effective revision is the identity
  carried by the effective-booking snapshot (Section 24) and used by capacity
  computation (Sections 10-14, 26).
- If every authoritative collision winner of a booking is `voided`, the booking
  has **no effective revision** and contributes nothing at all.
- A `cancelled` effective revision remains non-active unless a later revision
  becomes effective.
- Composition: the effective revision (term 4 of Section 3.1) is the state
  consumed by the active-booking rule (Section 8), by the effective-booking
  snapshot (Section 24), by the `is_effective` flag of the history
  (Section 25), and by capacity computation (Sections 10-14, 26).

## 8. Active bookings

- A booking is **active** if and only if its effective revision (term 4 of
  Section 3.1, Section 7) has status `booked` (Section 6). An active booking
  is an **effective BOOKED booking** (term 5 of Section 3.1).
- A booking whose effective revision is `cancelled` is not active. A booking
  with no effective revision is not active. Voided revisions are never active.
- Active bookings are the population from which capacity is computed
  (Sections 10-14, 26). A booking that is not active contributes no capacity
  and no facts row.

## 9. Intervals

- An effective revision's interval runs from its `start_date`/`start_time`
  instant to its `end_date`/`end_time` instant.
- The interval is **half-open** `[start, end)`: it occupies every minute `m`
  such that `start <= m < end`. The instant `end` itself is not occupied.
- Times are normalized to the minute (seconds truncated). A minute is occupied
  if its whole minute-long slot falls within the half-open interval.
- `end` equal to `start` is a valid zero-length interval that blocks nothing:
  it occupies no minute and therefore contributes no blocked minute on any
  calendar day (Sections 10, 14) and is never a positive-minute capacity
  contributor (term 6 of Section 3.1).
- `end` before `start` is malformed input (Section 16).
- Composition: the interval semantics of this section are consumed by
  calendar-day materialization (Section 10), by the shared-room union
  (Section 12), and by the capacity reconciliation (Section 26).

## 10. Calendar-day materialization

- A minute belongs to the calendar day in which it starts. Minute `00:00` of a
  day is that day's first minute.
- The blocked minutes contributed by one booking on one calendar day `D` is the
  count of minutes `m` that are inside the booking's interval (Section 9) and
  inside day `D`.
- Multi-day intervals therefore spread naturally across days: minutes after
  midnight belong to the new day. A day with zero blocked minutes from a
  booking contributes nothing for that booking.
- Composition: this section defines the per-day attribution consumed by the
  daily fact table (Section 14), by the shared-room union (Section 12), by the
  reconciliation artifact (Section 26), and by the `multi_day_active_bookings`
  metric (Section 17).

## 11. Capacity grain

- The capacity unit is the pair `(clinic_id, room_id)`. Room identifiers are
  unique only within a clinic: the same `room_id` in two different clinics is
  two different rooms with independent capacity. The canonical `clinic_id` and
  `room_id` are those resolved by Sections 20-21 for the effective revision
  (Section 7).
- Capacity is not split by any other field (Section 13).
- Composition: this section defines the grain used by the daily fact table
  (Section 14), the shared-room union (Section 12), and the reconciliation
  artifact (Section 26).

## 12. Shared-timeline overlap resolution

- One room can serve only one reservation at a time.
- When two or more active bookings (Section 8) on the same `(clinic_id, room_id)`
  overlap in time, the room is still only one room. A minute blocked by any
  active booking on that room is blocked exactly once; overlapping bookings do
  not add capacity. The room's blocked time is the **union** of the blocked
  intervals (Section 9) of all active bookings on that room, never their sum.
- Overlap resolution applies jointly to **all** active bookings on the
  `(clinic_id, room_id)` pair, including bookings recorded under different
  providers and different sources (Section 13), and it applies to the final
  effective intervals (Section 7) of those bookings (never to superseded
  revisions, cancellations, or voided rows).
- Two intervals that only touch (one ends at exactly the instant the other
  begins) do not overlap; both minutes are blocked, counted once each.
- Composition: this section defines the shared-timeline union consumed by the
  daily fact table (Section 14), by the `blocked_minutes` of the reconciliation
  artifact (Section 26), and by the `overlap_minutes` computation there.

## 13. Provider and source

- `provider_id` and `source` are descriptive attributes of a booking line. They
  do not create separate capacity universes and never appear in the output.
- Two active bookings (Section 8) on the same `(clinic_id, room_id)` compete
  for the same room regardless of provider or source.
- Composition: this section's non-partition rule is consumed by the shared-room
  union (Section 12), the daily fact table (Section 14), and the reconciliation
  artifact (Section 26).

## 14. Daily aggregation

- For every `(clinic_id, room_id, calendar_date)` combination, `blocked_minutes`
  is the number of minutes on that day blocked by at least one active booking
  (Section 8) on that room (the day-length of the union described in
  Section 12, with per-day attribution per Section 10).
- The fact table contains a row **only** for combinations with
  `blocked_minutes >= 1`.
- Composition: this section defines the daily fact table consumed by the
  reconciliation artifact (Section 26) and by the `capacity_quality` metrics
  (Section 17).

## 15. Output CSV

`/app/output/daily_room_capacity_facts.csv`

- UTF-8, no byte-order mark, a header row, and one record per line.
- Columns, in order: `room_id`, `clinic_id`, `calendar_date`, `blocked_minutes`.
  - `room_id` and `clinic_id` are the canonical (trimmed, upper-case) values
    (Sections 20-21).
  - `calendar_date` is `YYYY-MM-DD`.
  - `blocked_minutes` is a non-negative integer.
- Rows are sorted ascending by `room_id` (string order), then `clinic_id`
  (string order), then `calendar_date` (chronological).

## 16. Malformed input

A row is **malformed** if, after normalization, any of these hold:

- `event_id` or `booking_id` is empty;
- `revision` is not a non-negative whole number;
- `status` is not one of `booked`, `cancelled`, `voided`;
- `clinic_id` or `room_id` is empty;
- `provider_id` or `source` may be empty; any other field may not;
- `start_date` or `end_date` is not a `YYYY-MM-DD` date;
- `start_time` or `end_time` is not a valid `HH:MM` or `HH:MM:SS` time;
- `created_ts` is not a valid `YYYY-MM-DD HH:MM:SS` timestamp;
- the end instant is earlier than the start instant.

Malformed rows are excluded from all capacity computation and are counted in
`malformed_rows_rejected` (Section 17). Rejecting a malformed row never
invalidates other, well-formed rows of the same booking. Malformed-row detection
is applied to the **deduplication survivors** (term 1 of Section 3.1): the
deduplication-surviving copy of a duplicated event is the one validated. The
rows that pass this section are the **validated surviving rows** (term 2 of
Section 3.1), which are the collision population of Section 5. The malformed
rules apply uniformly to every event source; an adjustment row that fails these
rules is rejected in the same way as a core-feed row.

## 17. Quality summary

`/app/output/quality_summary.json` is a single JSON object containing exactly
**five top-level categories**, in this exact order:

`event_quality`, `identity_quality`, `revision_quality`,
`capacity_quality`, `reconciliation_quality`.

Each category is an object whose keys appear in the exact order listed below.
Every value is an exact integer. The categories, keys, and values are fixed;
the object contains no extra categories and no extra keys.

### Population lineage

The quality summary is a summary of the population lineage defined across the
stages of this contract. Each stage produces a population that is a subset of,
or a refinement of, the previous one. The definitions of the terms below are in
Section 3.1; the stages that produce each population are cited beside it.

1. **raw rows** (Section 2) - every row across all event sources
   (`scheduling_events.csv` and `reservation_adjustments.ndjson`), including
   duplicates and malformed rows.
2. **deduplication survivors** (term 1 of Section 3.1) - raw rows minus every
   copy of an `event_id` beyond the first (Section 4).
3. **validated surviving rows** (term 2 of Section 3.1) - deduplication
   survivors that pass the malformed-input rules of Section 16.
4. **authoritative collision winners** (term 3 of Section 3.1) - for each
   `(booking_id, revision)`, the validated surviving row selected by Section 5
   collision resolution. Rows that lose a collision remain in the validated
   surviving row population; collision resolution does not remove them.
5. **effective revisions** (term 4 of Section 3.1) - per booking, the highest
   non-voided authoritative collision winner (Section 7), or none.
6. **active effective bookings** (term 5 of Section 3.1) - effective revisions
   whose status is `booked` (Section 8).
7. **materialized active intervals** - per-calendar-day pieces of active
   effective bookings (Section 10).
8. **room-day union intervals** - the per-`(clinic_id, room_id, day)` union of
   materialized active intervals (Sections 11-12).

Each quality-summary metric is computed over the population explicitly
specified for that metric below, using the term definitions of Section 3.1.
Later transformations do not redefine earlier population counts: each metric
uses only its stated population. A booking that never reaches the validated
surviving rows (every deduplication survivor is malformed) is not part of any
booking-level metric.

### event_quality

| Key | Type | Population | Aggregation |
|-----|------|-----------|-------------|
| `total_raw_events` | integer | 1. raw rows | Count of every data row across all event sources, including duplicates and malformed rows. |
| `duplicate_events_removed` | integer | transition 1 to 2 | Count of rows discarded by the `event_id` deduplication (every copy of an `event_id` beyond the first, across all event sources). |
| `malformed_rows_rejected` | integer | transition 2 to 3 | Count of rows, after deduplication, that fail the malformed-input rules of Section 16. |
| `adjustment_rows_valid` | integer | 3. validated surviving rows, filtered to rows whose normalized `source` is `ADJUSTMENT` | Count of validated surviving rows that came from `reservation_adjustments.ndjson` (identified by their `source` value `ADJUSTMENT`). |
| `adjustment_rows_rejected` | integer | 1. raw rows, filtered to rows whose normalized `source` is `ADJUSTMENT`, minus `adjustment_rows_valid` | Count of adjustment-feed rows that do not reach the validated surviving rows (whether discarded as duplicates or rejected as malformed). Equivalently, total adjustment rows minus `adjustment_rows_valid`. |

### identity_quality

The population for every metric below is **3. validated surviving rows** (term
2 of Section 3.1). Each metric counts rows of that population according to the
identity resolution of Sections 20-21.

| Key | Type | Population | Aggregation |
|-----|------|-----------|-------------|
| `clinic_aliases_resolved` | integer | 3. validated surviving rows | Count of validated rows whose normalized clinic token is present as a `clinic_alias` in `clinic_directory.csv` (i.e. resolves via a directory mapping rather than the fallback). |
| `room_aliases_resolved` | integer | 3. validated surviving rows | Count of validated rows whose normalized room token, looked up under its resolved canonical clinic, is present as a `room_alias` in `room_directory.csv` (i.e. resolves via a clinic-scoped directory mapping rather than the fallback). |
| `identity_tokens_unresolved` | integer | 3. validated surviving rows | Count of validated rows for which the clinic token is not a mapped alias OR the room token is not a clinic-scoped mapped alias (at least one fell back to its normalized token). |

### revision_quality

The populations referenced below are those of the lineage above (terms of
Section 3.1).

| Key | Type | Population | Aggregation |
|-----|------|-----------|-------------|
| `total_booking_ids` | integer | 3. validated surviving rows | Number of distinct `booking_id` values among the validated surviving rows. |
| `voided_revisions` | integer | 3. validated surviving rows | Number of validated surviving rows whose normalized `status` is `voided`. This includes VOIDED rows that later lose a `(booking_id, revision)` collision: collision resolution changes which row supplies authoritative revision content, but it does not retroactively remove collision-losing rows from the validated surviving row population used by this metric. Malformed rows do not appear in this population. |
| `bookings_with_effective_revision` | integer | 5. effective revisions | Number of bookings (from `total_booking_ids`) that have an effective revision (at least one non-voided authoritative collision winner). |
| `active_bookings` | integer | 6. active effective bookings | Number of bookings whose effective revision has status `booked`. This includes effective BOOKED bookings whose interval is zero-length. |
| `cancelled_bookings` | integer | 5. effective revisions | Number of bookings whose effective revision has status `cancelled`. |
| `bookings_with_no_effective_revision` | integer | 3. validated surviving rows | Number of bookings (from `total_booking_ids`) with no effective revision (Section 7): every authoritative collision winner of the booking is `voided`. |
| `multi_day_active_bookings` | integer | 6. active effective bookings | Number of active BOOKED effective bookings whose blocked minutes fall on more than one calendar day (Section 10). |

### capacity_quality

The population for every metric below is **8. room-day union intervals**
(Sections 10-14, 26).

| Key | Type | Population | Aggregation |
|-----|------|-----------|-------------|
| `total_blocked_minutes` | integer | 8. room-day union intervals | Sum of `blocked_minutes` over every row of the output fact table. |
| `room_clinic_days_with_overlap` | integer | 8. room-day union intervals | Number of `(clinic_id, room_id, calendar_date)` combinations for which at least two active bookings blocked at least one shared minute; equivalently, combinations whose day union minutes are strictly fewer than the sum of the individual active bookings' minutes on that day. |
| `overlap_minutes_total` | integer | 8. room-day union intervals | Sum of `overlap_minutes` over every row of the capacity reconciliation artifact (each row's `overlap_minutes` = `raw_interval_minutes - blocked_minutes`). |

### reconciliation_quality

The population for every metric below is **8. room-day union intervals**
(Section 26).

| Key | Type | Population | Aggregation |
|-----|------|-----------|-------------|
| `reconciled_room_days` | integer | 8. room-day union intervals | Number of rows in the capacity reconciliation artifact. |
| `reconciliation_mismatches` | integer | 8. room-day union intervals | Count of reconciliation rows for which the conservation invariant is violated: `raw_interval_minutes != blocked_minutes + overlap_minutes`, or `overlap_minutes < 0`. For a correct pipeline this is `0`. |

The number of rows in the output fact table is deliberately **not** a JSON key:
it is a separate quantity that can be derived from the CSV itself (the count of
data rows in `/app/output/daily_room_capacity_facts.csv` after the header), and
it is not part of the summary object.

Consistency identity: `total_booking_ids =
bookings_with_effective_revision + bookings_with_no_effective_revision`, and
`active_bookings + cancelled_bookings = bookings_with_effective_revision`.

## 18. JSON serialization

- UTF-8, standard JSON. The key set, the key order (Section 17), and the integer
  values are fixed. Formatting whitespace is not specified.
- `blocked_minutes` and every quality-summary value are exact integers; no
  rounding is applied anywhere in the pipeline.

## 19. General assumptions

- All dates and times are local wall-clock time; no time zone, DST, or calendar
  other than the Gregorian `YYYY-MM-DD` is involved.
- The four source artifacts together are the complete operational event and
  identity data; no other source of bookings or room/clinic identity exists.
- Duplicate, malformed, superseded, cancelled, and voided content never add
  capacity and never create output rows.
- The pipeline produces the outputs at the paths given in Sections 15, 24, 25,
  and 26, plus the quality summary JSON of Section 17.

## 20. Clinic identity resolution

- Every clinic token carried by an event row is first normalized per Section 3
  (trimmed, case-insensitive, canonical upper-case form).
- The normalized clinic token is looked up in `clinic_directory.csv`
  (Section 2.3) as a `clinic_alias`. If a mapping exists, the canonical clinic
  is the corresponding `canonical_clinic_id`.
- If the normalized token is not listed as an alias, the canonical clinic is the
  normalized token itself (deterministic fallback).
- No fuzzy or approximate matching is ever used.
- Clinic resolution MUST precede room resolution (Section 21), because room
  aliases are scoped by clinic: the same room alias may map to different
  canonical rooms in different clinics.
- Composition: clinic identity is resolved per validated surviving row
  (term 2 of Section 3.1) before room identity (Section 21); the canonical
  clinic of a row is then carried into that row's role in collision
  (Section 5), effective-state selection (Section 7), and the outputs
  (Sections 24-26). The canonical clinic of the effective revision (Section 7)
  is the clinic used by every downstream artifact.

## 21. Room identity resolution

- Every room token carried by an event row is first normalized per Section 3.
- The room's clinic is resolved first (Section 20) to a canonical clinic.
- The pair `(canonical_clinic_id, normalized_room_token)` is looked up in
  `room_directory.csv` (Section 2.4). If a mapping exists, the canonical room is
  the corresponding `canonical_room_id`.
- Otherwise the canonical room is the normalized room token (deterministic
  fallback).
- A room alias may legitimately map to different canonical rooms under different
  canonical clinics.
- No fuzzy or approximate matching is ever used.
- Composition: room identity is resolved per validated surviving row
  (term 2 of Section 3.1), scoped by that row's canonical clinic (Section 20).
  The canonical room of the effective revision (Section 7) is the room used by
  the effective-booking snapshot (Section 24), the history (Section 25), the
  daily fact table (Sections 11, 14), and the reconciliation artifact
  (Section 26). Neither a raw room token's global lookup nor a previous
  revision's room is a substitute for resolving identity per row and then using
  the effective revision's resolved identity.

## 22. Source / attribute precedence

- **Canonical identity precedence** (Section 20 and Section 21):
  - `clinic_id`: clinic-directory mapping > normalized raw event token fallback.
  - `room_id`: clinic-scoped room-directory mapping > normalized raw event token
    fallback.
- **Booking revision content precedence:** for each `(booking_id, revision)`,
  the authoritative collision winner (Section 5, term 3 of Section 3.1)
  supplies `status`, `start_ts`, `end_ts`,
  `clinic_id`, `room_id`, `provider_id`, and `source`. The winner is the row
  with the latest `created_ts` across ALL validated surviving rows from all
  event sources; ties are broken by the greatest `event_id` (compared as
  strings); the winner is selected regardless of status, including `voided`.
- **Effective-revision precedence:** the effective revision is the
  highest-numbered authoritative revision whose status is not `voided`
  (Section 7).
- `provider_id` and `source` are descriptive attributes only; they never
  partition the capacity timeline (Section 13).
- Identity resolution (Sections 20-21), collision precedence (Section 5), and
  effective-revision precedence (Section 7) are distinct stages and must not
  be conflated. The canonical identity produced by Sections 20-21 for each
  validated surviving row is the identity stored in the authoritative
  collision winner (Section 5) and propagated through the effective revision
  (Section 7) into the effective/history/capacity outputs (Sections 24-26).

## 23. Canonical event ledger

- A canonical intermediate representation is produced from all event sources:
  `/app/work/canonical_events.csv`. It is an agent-produced work artifact and is
  not itself a required user-facing output.
- Required logical columns:

  | Column | Semantics |
  |--------|-----------|
  | `event_id` | Deduplication-surviving validated event id (unique). |
  | `booking_id` | Booking id. |
  | `revision` | Revision number. |
  | `status` | Canonical status. |
  | `clinic_id` | Canonical clinic id (Section 20). |
  | `room_id` | Canonical room id (Section 21). |
  | `provider_id` | Descriptive provider id. |
  | `source` | Source tag (attribute). |
  | `start_ts` | Canonical start instant. |
  | `end_ts` | Canonical end instant. |
  | `created_ts` | Recorded timestamp. |

- Semantic population: normalized, deduplicated, validated, and
  identity-resolved rows across all event sources (that is, the validated
  surviving rows of term 2 of Section 3.1 with identity resolved per
  Sections 20-21).
- `start_ts` and `end_ts` are serialized as `YYYY-MM-DD HH:MM:SS` (Section 3);
  normalized times always have seconds equal to `00`.
- Invariants:
  - `event_id` is unique after deduplication.
  - every accepted row has valid canonical date/time values;
  - when a directory mapping exists, the canonical room belongs to the resolved
    canonical clinic;
  - no downstream stage depends on a source-specific physical format or field
    name.

## 24. Effective booking snapshot

`/app/output/effective_bookings.csv`

- Logical schema:

  | Column | Semantics |
  |--------|-----------|
  | `booking_id` | Booking id. |
  | `status` | Effective status. |
  | `clinic_id` | Canonical clinic of the effective revision. |
  | `room_id` | Canonical room of the effective revision. |
  | `start_ts` | Effective interval start. |
  | `end_ts` | Effective interval end. |
  | `provider_id` | Descriptive provider id. |
  | `source` | Source tag (attribute). |
  | `effective_revision` | The revision number that is effective. |

- Population: the **effective revisions** of term 4 of Section 3.1 (selected
  per Section 7). There is one row per `booking_id` that has an effective
  revision; content comes only from that effective authoritative collision
  winner (term 3 of Section 3.1, Section 5).
- `booked` effective bookings (term 5 of Section 3.1) are the capacity-driving
  population; `cancelled` effective bookings remain represented for
  state/quality semantics but do not contribute blocked capacity.
- This artifact is the canonical current-state snapshot.
- `start_ts` and `end_ts` are serialized as `YYYY-MM-DD HH:MM:SS` (Section 3);
  normalized times always have seconds equal to `00`.
- Rows are sorted ascending by `booking_id` (string order).

## 25. Booking state history

`/app/output/booking_state_history.csv`

- Logical schema:

  | Column | Semantics |
  |--------|-----------|
  | `booking_id` | Booking id. |
  | `revision` | Revision number. |
  | `status` | Authoritative status of this revision. |
  | `clinic_id` | Canonical clinic of this revision. |
  | `room_id` | Canonical room of this revision. |
  | `start_ts` | Interval start of this revision. |
  | `end_ts` | Interval end of this revision. |
  | `effective_from` | Deterministic start of this revision's effective span (the authoritative collision winner's `created_ts`). |
  | `effective_to` | Deterministic end of this revision's effective span: the `created_ts` of the next higher-numbered authoritative revision of the same booking, or empty for the highest-numbered authoritative revision. |
  | `is_effective` | `1` exactly for the current effective revision when one exists, else `0`. |

- Population: the **authoritative collision winners** of term 3 of Section 3.1
  (Section 5); voided authoritative winners are represented according to the
  history semantics below.
- `is_effective = 1` marks exactly the current effective revision (term 4 of
  Section 3.1, Section 7) when one exists; a booking whose every authoritative
  collision winner is `voided` has no effective row (`is_effective` is never
  `1` for it).
- `effective_from`/`effective_to` are deterministic and derived from the
  authoritative revision chronology: `effective_from` is the authoritative
  collision winner's `created_ts`, and `effective_to` is the `created_ts` of the next
  higher-numbered authoritative collision winner of the same booking, or empty for the
  highest-numbered authoritative collision winner.
- `start_ts`, `end_ts`, `effective_from`, and `effective_to` are serialized as
  `YYYY-MM-DD HH:MM:SS` (Section 3); normalized times always have seconds equal
  to `00`. `effective_to` is empty for the highest-numbered authoritative
  revision.
- Rows are sorted ascending by `booking_id` (string order), then by `revision`
  (ascending integer order).

## 26. Capacity reconciliation

`/app/output/capacity_reconciliation.csv`

- Logical schema:

  | Column | Semantics |
  |--------|-----------|
  | `room_id` | Canonical room id. |
  | `clinic_id` | Canonical clinic id. |
  | `calendar_date` | Calendar date. |
  | `effective_booking_count` | Number of effective BOOKED bookings contributing at least one minute on that room-day. |
  | `raw_interval_minutes` | Sum of the per-day blocked minutes for each contributing effective BOOKED booking before union-merging. |
  | `blocked_minutes` | Shared-room union minutes, equal to the corresponding `daily_room_capacity_facts.csv` value. |
  | `overlap_minutes` | `raw_interval_minutes - blocked_minutes`. |

- Population: the **reconciliation room-days** of term 7 of Section 3.1, which
  are the `(room_id, clinic_id, calendar_date)` combinations having at least
  one positive-minute capacity contributor (term 6 of Section 3.1). The
  contributing bookings are the **effective BOOKED bookings** of term 5 of
  Section 3.1 whose materialized intervals (Section 10) fall on that room-day;
  their intervals (Section 9) are unioned per Section 12.
- Conservation invariant: `raw_interval_minutes =
  blocked_minutes + overlap_minutes` for every row.
- A room/day reconciliation row exists only when at least one effective BOOKED
  booking contributes at least one blocked minute on that day.
- Rows are sorted ascending by `room_id` (string order), then `clinic_id`
  (string order), then `calendar_date` (chronological).

## 27. Cross-artifact invariants

- **A. Effective-booking identity:** every `effective_bookings.csv` row
  corresponds to exactly one effective revision (term 4 of Section 3.1) in the
  authoritative collision-winner ledger.
- **B. History/current-state identity:** for every booking with an effective
  revision, the history row with `is_effective = 1` equals the corresponding
  `effective_bookings.csv` row.
- **C. Capacity provenance:** every fact-table blocked minute is explainable by
  at least one effective BOOKED booking (term 5 of Section 3.1) interval.
- **D. Capacity reconciliation:** `raw_interval_minutes =
  blocked_minutes + overlap_minutes`.
- **E. Fact/reconciliation equality:** `reconciliation.blocked_minutes` equals
  `daily_room_capacity_facts.blocked_minutes` for the same room-day.
- **F. Effective-booking count:** `reconciliation.effective_booking_count`
  equals the independently derived count of effective BOOKED bookings
  contributing at least one minute that day.
- **G. Shared-room invariant:** `provider_id`/`source` differences do not create
  separate capacity timelines.
- **H. Superseded-state invariant:** superseded revisions never contribute
  capacity.
- **I. No phantom capacity:** `cancelled` or all-`voided` effective states never
  contribute blocked minutes.
- **J. Deterministic ordering:** all output sort orders and JSON key orders are
  fixed.

## 28. Structured dictionary

- `fact_dictionary.toml` (Section 1) is a machine-readable representation of
  source schemas, normalization rules, identity mappings, precedence, revision
  semantics, output schemas, metrics, and invariants.
- It must not contradict this contract; this contract remains the authoritative
  semantic prose. Where the dictionary and contract disagree, this contract
  wins.

## 29. Quality metric categories

- The quality summary is organized into exactly five deterministic categories,
  each with a fixed key set and key order, as normatively defined in
  Section 17:

  | Category | Coverage |
  |----------|----------|
  | `event_quality` | raw/duplicate/malformed counts, adjustment validity. |
  | `identity_quality` | clinic and room alias resolution counts. |
  | `revision_quality` | booking-level revision/state counts. |
  | `capacity_quality` | blocked-minute and overlap totals. |
  | `reconciliation_quality` | reconciled room-day counts and mismatches. |

- Every metric has an exact source population, an exact aggregation, and an
  exact deterministic integer type, all specified in Section 17 using the term
  definitions of Section 3.1.
- Section 17 is the sole authoritative definition of the quality-summary JSON
  schema; it supersedes any earlier flat-key representation.
