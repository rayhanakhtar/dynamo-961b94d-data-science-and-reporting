# Fact Contract — Daily Room Capacity Facts

This document is the authoritative specification for converting ambulatory
scheduling / exam-room reservation operational information into a daily
room-capacity fact table, an effective-booking snapshot, a booking-state
history, a capacity reconciliation, and a categorized data-quality summary.
A correct implementation must satisfy every rule in this document. When this
contract and any other description disagree, this contract wins.

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
  said to **survive deduplication**; a discarded copy does not.

## 5. Booking identity

- A logical booking is identified by its `booking_id`. Every row that carries a
  `booking_id` and a `revision` is one *revision event* of that booking.
- Revisions of one booking are ordered by their integer `revision` value.
  Revision numbers need not be contiguous.
- If two or more rows carry the same `(booking_id, revision)` pair, the row with
  the latest `created_ts` is authoritative for that revision; if `created_ts`
  ties, the row with the greatest `event_id` (compared as strings) wins.
  Collision resolution operates on the union of validated surviving rows across
  **all** event sources (Section 4): a `scheduling_events.csv` row and a
  `reservation_adjustments.ndjson` row for the same `(booking_id, revision)`
  compete under the same rule.
- Collision resolution is applied before effective-revision selection: the
  **authoritative row** for a revision (the row chosen above) is that
  revision's sole content for effective-revision selection and for all
  capacity computation. Collision resolution does not remove the losing rows
  from the population of rows that survived deduplication (Section 4); those
  losing rows remain eligible rows for the quality-summary counts of
  Section 17.

## 6. Status semantics

Each revision event has exactly one status:

- `booked` — the booking occupies the room for the interval in that row.
- `cancelled` — as of this revision the booking no longer occupies the room.
- `voided` — this revision was recorded in error and is annulled. A voided
  revision is treated as though it never existed: it does not change the
  booking's state and contributes no interval.

## 7. Effective revision

- A booking's **effective revision** is the highest-numbered revision whose
  authoritative content is not `voided`. A `voided` revision never provides the
  booking's final status, room, clinic, or interval, wherever it appears in the
  revision order.
- The booking's final state and interval come only from its effective revision.
- The effective revision's row also fixes the booking's `clinic_id` and
  `room_id`: the booking's blocked minutes are attributed to the effective
  revision's `(clinic_id, room_id)`, never to a superseded revision's room or
  clinic.
- If every revision of a booking is `voided`, the booking has **no effective
  revision** and contributes nothing at all.
- A `cancelled` effective revision remains non-active unless a later revision
  becomes effective.

## 8. Active bookings

- A booking is **active** if and only if its effective revision's status is
  `booked`.
- A booking whose effective revision is `cancelled` is not active. A booking
  with no effective revision is not active. Voided revisions are never active.

## 9. Intervals

- An effective revision's interval runs from its `start_date`/`start_time`
  instant to its `end_date`/`end_time` instant.
- The interval is **half-open** `[start, end)`: it occupies every minute `m`
  such that `start <= m < end`. The instant `end` itself is not occupied.
- Times are normalized to the minute (seconds truncated). A minute is occupied
  if its whole minute-long slot falls within the half-open interval.
- `end` equal to `start` is a valid zero-length interval that blocks nothing.
- `end` before `start` is malformed input (Section 16).

## 10. Calendar-day materialization

- A minute belongs to the calendar day in which it starts. Minute `00:00` of a
  day is that day's first minute.
- The blocked minutes contributed by one booking on one calendar day `D` is the
  count of minutes `m` that are inside the booking's interval and inside day `D`.
- Multi-day intervals therefore spread naturally across days: minutes after
  midnight belong to the new day. A day with zero blocked minutes from a
  booking contributes nothing for that booking.

## 11. Capacity grain

- The capacity unit is the pair `(clinic_id, room_id)`. Room identifiers are
  unique only within a clinic: the same `room_id` in two different clinics is
  two different rooms with independent capacity.
- Capacity is not split by any other field.

## 12. Shared-timeline overlap resolution

- One room can serve only one reservation at a time.
- When two or more active bookings on the same `(clinic_id, room_id)` overlap in
  time, the room is still only one room. A minute blocked by any active booking
  on that room is blocked exactly once; overlapping bookings do not add
  capacity. The room's blocked time is the **union** of the blocked intervals
  of all active bookings on that room, never their sum.
- Overlap resolution applies jointly to **all** active bookings on the
  `(clinic_id, room_id)` pair, including bookings recorded under different
  providers and different sources, and it applies to the final effective
  intervals of those bookings (never to superseded revisions, cancellations, or
  voided rows).
- Two intervals that only touch (one ends at exactly the instant the other
  begins) do not overlap; both minutes are blocked, counted once each.

## 13. Provider and source

- `provider_id` and `source` are descriptive attributes of a booking line. They
  do not create separate capacity universes and never appear in the output.
- Two active bookings on the same `(clinic_id, room_id)` compete for the same
  room regardless of provider or source.

## 14. Daily aggregation

- For every `(clinic_id, room_id, calendar_date)` combination, `blocked_minutes`
  is the number of minutes on that day blocked by at least one active booking on
  that room (the day-length of the union described in Section 12).
- The fact table contains a row **only** for combinations with
  `blocked_minutes >= 1`.

## 15. Output CSV

`/app/output/daily_room_capacity_facts.csv`

- UTF-8, no byte-order mark, a header row, and one record per line.
- Columns, in order: `room_id`, `clinic_id`, `calendar_date`, `blocked_minutes`.
  - `room_id` and `clinic_id` are the canonical (trimmed, upper-case) values.
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
is applied to the rows that survive deduplication (Section 4): the surviving
copy of a duplicated event is the one validated. The malformed rules apply
uniformly to every event source; an adjustment row that fails these rules is
rejected in the same way as a core-feed row.

## 17. Quality summary

`/app/output/quality_summary.json` is a single JSON object containing exactly
**twelve keys**, each with an integer value. The keys appear in this order:

### Population provenance

The pipeline transforms the raw export through a sequence of distinct
populations. Each stage produces a population that is a subset of, or a
refinement of, the previous one:

1. **raw rows** — every row across all event sources (`scheduling_events.csv`
   and `reservation_adjustments.ndjson`).
2. **deduplicated survivors** — raw rows minus every copy of an `event_id`
   beyond the first (Section 4).
3. **validated survivors** — deduplicated survivors that pass the
   malformed-input rules of Section 16.
4. **authoritative revision rows** — for each `(booking_id, revision)`, the
   validated survivor selected by Section 5 collision resolution. Rows that
   lose a collision remain in the validated population; collision resolution
   does not remove them.
5. **effective revisions** — per booking, the highest non-voided authoritative
   revision row (Section 7), or none.
6. **active effective bookings** — effective revisions whose status is
   `booked` (Section 8).
7. **materialized active intervals** — per-calendar-day pieces of active
   effective bookings (Section 10).
8. **room-day union intervals** — the per-`(clinic_id, room_id, day)` union of
   materialized active intervals (Sections 11-12).

Each quality-summary metric is computed over the population explicitly
specified for that metric in the table below, named by the stage (or stage
transition) that produces it. Later transformations do not redefine earlier
population counts: each metric uses only its stated population.

| Key | Meaning | Population |
|-----|---------|-----------|
| `total_raw_events` | Number of data rows across all event sources, including duplicates and malformed rows. | 1. raw rows. |
| `duplicate_events_removed` | Number of rows discarded by the `event_id` deduplication (every copy of an `event_id` beyond the first). | transition 1 to 2. |
| `malformed_rows_rejected` | Number of rows, after deduplication, that fail the malformed-input rules of Section 16. | transition 2 to 3. |
| `total_booking_ids` | Number of distinct `booking_id` values in the validated survivors. | 3. validated survivors. |
| `voided_revisions` | Number of rows in the validated survivors whose normalized `status` is `voided`. Collision losers remain in this population; malformed rows do not. | 3. validated survivors. |
| `bookings_with_effective_revision` | Number of distinct bookings (from `total_booking_ids`) that have at least one non-voided authoritative revision row. | 3. validated survivors. |
| `active_bookings` | Number of bookings whose effective revision has status `booked`. | 6. active effective bookings. |
| `cancelled_bookings` | Number of bookings whose effective revision has status `cancelled`. | 5. effective revisions. |
| `bookings_with_no_effective_revision` | Number of bookings (from `total_booking_ids`) with no effective revision (Section 7): every authoritative revision row of the booking (Section 5) is `voided`. | 3. validated survivors. |
| `multi_day_active_bookings` | Number of active bookings whose blocked minutes fall on more than one calendar day (Section 10). | 6. active effective bookings. |
| `room_clinic_days_with_overlap` | Number of `(clinic_id, room_id, calendar_date)` combinations on which at least two active bookings blocked at least one shared minute (equivalently, combinations where the day's union minutes are strictly fewer than the sum of the individual active bookings' minutes on that day). | 8. room-day union intervals. |
| `total_blocked_minutes` | Sum of `blocked_minutes` over every row of the output fact table. | 8. room-day union intervals. |

A booking that never reaches the validated survivors (every surviving row is
malformed) is not part of `total_booking_ids` and is counted by none of the
booking-level metrics.

The quality summary contains the twelve keys above and no others. The number of
rows in the output fact table is deliberately **not** a JSON key: it is a
separate quantity that can be derived from the CSV itself (the count of data
rows in `/app/output/daily_room_capacity_facts.csv` after the header), and it
is not part of the summary object.

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

## 22. Source / attribute precedence

- **Canonical identity precedence** (Section 20 and Section 21):
  - `clinic_id`: clinic-directory mapping > normalized raw event token fallback.
  - `room_id`: clinic-scoped room-directory mapping > normalized raw event token
    fallback.
- **Booking revision content precedence:** for each `(booking_id, revision)`,
  the authoritative row (Section 5) supplies `status`, `start_ts`, `end_ts`,
  `clinic_id`, `room_id`, `provider_id`, and `source`. The winner is the row
  with the latest `created_ts` across ALL validated surviving rows from all
  event sources; ties are broken by the greatest `event_id` (compared as
  strings); the winner is selected regardless of status, including `voided`.
- **Effective-revision precedence:** the effective revision is the
  highest-numbered authoritative revision whose status is not `voided`
  (Section 7).
- `provider_id` and `source` are descriptive attributes only; they never
  partition the capacity timeline (Section 13).
- Identity resolution, collision precedence, and effective-revision precedence
  are distinct stages and must not be conflated.

## 23. Canonical event ledger

- A canonical intermediate representation is produced from all event sources:
  `/app/work/canonical_events.csv`. It is an agent-produced work artifact and is
  not itself a required user-facing output.
- Required logical columns:

  | Column | Semantics |
  |--------|-----------|
  | `event_id` | Surviving validated event id (unique). |
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
  identity-resolved rows across all event sources.
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

- Population: one row per `booking_id` that has an effective revision; content
  comes only from the effective authoritative revision (Section 7).
- `booked` effective bookings are the capacity-driving population; `cancelled`
  effective bookings remain represented for state/quality semantics but do not
  contribute blocked capacity.
- This artifact is the canonical current-state snapshot.

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
  | `effective_from` | Deterministic start of this revision's effective span (the authoritative row's `created_ts`). |
  | `effective_to` | Deterministic end of this revision's effective span: the `created_ts` of the next higher-numbered authoritative revision of the same booking, or empty for the highest-numbered authoritative revision. |
  | `is_effective` | `1` exactly for the current effective revision when one exists, else `0`. |

- Population: one row per authoritative revision; voided authoritative
  revisions are represented according to the history semantics below.
- `is_effective = 1` marks exactly the current effective revision when one
  exists; a booking whose every authoritative revision is `voided` has no
  effective row (`is_effective` is never `1` for it).
- `effective_from`/`effective_to` are deterministic and derived from the
  authoritative revision chronology: `effective_from` is the authoritative
  row's `created_ts`, and `effective_to` is the `created_ts` of the next
  higher-numbered authoritative revision of the same booking, or empty for the
  highest-numbered authoritative revision.

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

- Conservation invariant: `raw_interval_minutes =
  blocked_minutes + overlap_minutes` for every row.

## 27. Cross-artifact invariants

- **A. Effective-booking identity:** every `effective_bookings.csv` row
  corresponds to exactly one effective revision in the authoritative revision
  ledger.
- **B. History/current-state identity:** for every booking with an effective
  revision, the history row with `is_effective = 1` equals the corresponding
  `effective_bookings.csv` row.
- **C. Capacity provenance:** every fact-table blocked minute is explainable by
  at least one effective BOOKED booking interval.
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

- The quality summary (Section 17) is organized into deterministic categories:

  | Category | Coverage |
  |----------|----------|
  | `event_quality` | raw/duplicate/malformed counts, adjustment validity. |
  | `identity_quality` | clinic and room alias resolution counts. |
  | `revision_quality` | booking-level revision/state counts. |
  | `capacity_quality` | blocked-minute and overlap totals. |
  | `reconciliation_quality` | reconciled room-day counts and mismatches. |

- Every metric has an exact source population, an exact aggregation, and an
  exact deterministic integer type.
- The exact key set and values are finalized in the implementation phases; the
  twelve keys already specified in Section 17 remain the current validated
  quality-summary semantics.
