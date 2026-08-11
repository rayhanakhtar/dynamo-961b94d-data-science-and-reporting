# Fact Contract — Daily Room Capacity Facts

This document is the authoritative specification for converting the ambulatory
scheduling / exam-room reservation event export into a daily room-capacity fact
table and a data-quality summary. A correct implementation must satisfy every
rule in this document. When this contract and any other description disagree,
this contract wins.

## 1. Inputs

| Path | Contents |
|------|----------|
| `/app/data/scheduling_events.csv` | The event export (UTF-8 CSV, one header row, then one record per line). |
| `/app/data/docs/fact_contract.md` | This contract. |

## 2. Input schema

The export has exactly these 13 columns, in this order:

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
- The export may contain the same raw event more than once (an artifact of how
  the export was assembled). For each `event_id`, only the first copy in file
  order is kept; every later copy of the same `event_id` is a duplicate and is
  discarded. Each discarded copy counts toward `duplicate_events_removed`
  (Section 18).

## 5. Booking identity

- A logical booking is identified by its `booking_id`. Every row that carries a
  `booking_id` and a `revision` is one *revision event* of that booking.
- Revisions of one booking are ordered by their integer `revision` value.
  Revision numbers need not be contiguous.
- If two or more rows carry the same `(booking_id, revision)` pair, the row with
  the latest `created_ts` is authoritative for that revision; if `created_ts`
  ties, the row with the greatest `event_id` (compared as strings) wins.
- Collision resolution is applied before effective-revision selection; the
  surviving row for a revision is that revision's sole content.

## 6. Status semantics

Each revision event has exactly one status:

- `booked` — the booking occupies the room for the interval in that row.
- `cancelled` — as of this revision the booking no longer occupies the room.
- `voided` — this revision was recorded in error and is annulled. A voided
  revision is treated as though it never existed: it does not change the
  booking's state and contributes no interval.

## 7. Effective revision

- Scan a booking's revisions in descending order of `revision` value, skipping
  every `voided` revision. The first revision encountered whose status is
  `booked` or `cancelled` is the booking's **effective revision**.
- The booking's final state and interval come only from its effective revision.
- If every revision of a booking is `voided`, the booking has **no effective
  revision** and contributes nothing at all.
- Consequences of these rules:
  - A `voided` revision is skipped wherever it appears in the revision order,
    even if it is the highest-numbered revision; it never provides the
    booking's interval or status.
  - A booking whose highest-numbered revision is `voided` is governed by the
    highest-numbered revision that is not `voided` (typically the previous
    revision).
  - A `cancelled` effective revision is never undone by an earlier revision;
    only a later revision can change the booking's state (for example, a
    re-booking).

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
  count of minutes `m` that are inside the booking's interval and inside day `D`
  (that is, the length of the intersection of the booking's interval with the
  day, measured in minutes).
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
copy of a duplicated event is the one validated.

## 17. Quality summary

`/app/output/quality_summary.json` is a single JSON object containing exactly
**twelve keys**, each with an integer value. The keys appear in this order:

| Key | Meaning |
|-----|---------|
| `total_raw_events` | Number of data rows in `/app/data/scheduling_events.csv`, including duplicates and malformed rows. |
| `duplicate_events_removed` | Number of rows discarded by the `event_id` deduplication (every copy of an `event_id` beyond the first). |
| `malformed_rows_rejected` | Number of rows, after deduplication, that fail the malformed-input rules of Section 16. |
| `total_booking_ids` | Number of distinct `booking_id` values among the rows that survive deduplication and are not malformed. |
| `voided_revisions` | Number of surviving, well-formed rows whose `status` is `voided`. |
| `bookings_with_effective_revision` | Number of distinct bookings (from `total_booking_ids`) that have at least one non-voided revision. |
| `active_bookings` | Number of bookings whose effective revision has status `booked`. |
| `cancelled_bookings` | Number of bookings whose effective revision has status `cancelled`. |
| `bookings_with_no_effective_revision` | Number of bookings all of whose surviving revisions are `voided`. |
| `multi_day_active_bookings` | Number of active bookings whose blocked minutes fall on more than one calendar day. |
| `room_clinic_days_with_overlap` | Number of `(clinic_id, room_id, calendar_date)` combinations on which at least two active bookings blocked at least one shared minute (equivalently, combinations where the day's union minutes are strictly fewer than the sum of the individual active bookings' minutes on that day). |
| `total_blocked_minutes` | Sum of `blocked_minutes` over every row of the output fact table. |

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
- The export is the complete set of events; no other source of bookings exists.
- Duplicate, malformed, and superseded content never add capacity and never
  create output rows.
- The pipeline produces exactly two outputs: the fact table CSV and the quality
  summary JSON, at the paths given in Sections 15 and 17.
