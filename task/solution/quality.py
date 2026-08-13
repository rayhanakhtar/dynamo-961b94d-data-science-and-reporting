"""Categorized quality report (contract sections 17, 29).

Deterministic integer metrics grouped into event_quality, identity_quality,
revision_quality, capacity_quality, and reconciliation_quality. Each metric is
derived from its contract-designated population.
"""

from materialize import count_multi_day


def build_categorical_summary(total_raw, duplicates, malformed, records,
                              effective, fact_rows, reconciliation,
                              identity_stats, adjustment_total):
    """Build the categorized quality summary dict with deterministic order."""
    effective_list = list(effective.values())

    adjustment_valid = sum(1 for rec in records
                           if rec["source"] == "ADJUSTMENT")

    event_quality = {
        "total_raw_events": total_raw,
        "duplicate_events_removed": duplicates,
        "malformed_rows_rejected": malformed,
        "adjustment_rows_valid": adjustment_valid,
        "adjustment_rows_rejected": adjustment_total - adjustment_valid,
    }

    identity_quality = dict(identity_stats)

    revision_quality = {
        "total_booking_ids": len(effective),
        "voided_revisions": sum(1 for rec in records
                                if rec["status"] == "VOIDED"),
        "bookings_with_effective_revision":
            sum(1 for rec in effective_list if rec is not None),
        "active_bookings":
            sum(1 for rec in effective_list
                if rec is not None and rec["status"] == "BOOKED"),
        "cancelled_bookings":
            sum(1 for rec in effective_list
                if rec is not None and rec["status"] == "CANCELLED"),
        "bookings_with_no_effective_revision":
            sum(1 for rec in effective_list if rec is None),
        "multi_day_active_bookings": count_multi_day(effective),
    }

    capacity_quality = {
        "total_blocked_minutes": sum(minutes
                                     for _r, _c, _d, minutes in fact_rows),
        "room_clinic_days_with_overlap": sum(
            1 for r in reconciliation if r["raw_interval_minutes"] >
            r["blocked_minutes"]),
        "overlap_minutes_total": sum(r["overlap_minutes"]
                                     for r in reconciliation),
    }

    reconciliation_mismatches = sum(
        1 for r in reconciliation
        if r["raw_interval_minutes"] != r["blocked_minutes"] +
        r["overlap_minutes"] or r["overlap_minutes"] < 0)

    reconciliation_quality = {
        "reconciled_room_days": len(reconciliation),
        "reconciliation_mismatches": reconciliation_mismatches,
    }

    return {
        "event_quality": event_quality,
        "identity_quality": identity_quality,
        "revision_quality": revision_quality,
        "capacity_quality": capacity_quality,
        "reconciliation_quality": reconciliation_quality,
    }
