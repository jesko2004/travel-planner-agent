from datetime import timedelta

import pytest

from travel_planner.models import (
    EvidenceKind,
    LockedActivitySnapshot,
    ValidationIssue,
    VerificationStatus,
)
from travel_planner.services.validator import ItineraryValidator


def _codes(issues):
    return {issue.code for issue in issues}


def test_verified_itinerary_has_no_errors(verified_itinerary, validation_context):
    validator = ItineraryValidator()
    validator.apply_status(verified_itinerary, validation_context)
    assert verified_itinerary.status == VerificationStatus.VERIFIED
    assert not [
        issue for issue in verified_itinerary.validation_issues if issue.severity == "error"
    ]
    assert verified_itinerary.budget.activities == 150


def test_missing_route_prevents_verified_status(verified_itinerary, validation_context):
    verified_itinerary.days[0].route_legs.clear()
    ItineraryValidator().apply_status(verified_itinerary, validation_context)
    assert verified_itinerary.status == VerificationStatus.UNVERIFIED
    assert "MISSING_ROUTE" in _codes(verified_itinerary.validation_issues)


def test_time_overlap_is_detected(verified_itinerary, validation_context):
    verified_itinerary.days[0].activities[1].start_time = (
        verified_itinerary.days[0].activities[0].start_time
    )
    issues = ItineraryValidator().validate(verified_itinerary, validation_context)
    assert "TIME_OVERLAP" in _codes(issues)


def test_incomplete_required_stage_has_highest_draft_priority(
    verified_itinerary, validation_context
):
    validation_context.required_stages["enrich_routes"] = False
    verified_itinerary.days[0].route_legs.clear()
    ItineraryValidator().apply_status(verified_itinerary, validation_context)
    assert verified_itinerary.status == VerificationStatus.DRAFT
    assert "REQUIRED_STAGE_INCOMPLETE" in _codes(verified_itinerary.validation_issues)


def test_missing_required_stage_names_cannot_be_bypassed(
    verified_itinerary, validation_context
):
    validation_context.required_stages = {"some_other_stage": True}
    ItineraryValidator().apply_status(verified_itinerary, validation_context)
    assert verified_itinerary.status == VerificationStatus.DRAFT
    assert "REQUIRED_STAGE_INCOMPLETE" in _codes(verified_itinerary.validation_issues)


def test_missing_tool_record_cannot_be_verified(verified_itinerary, validation_context):
    poi = verified_itinerary.days[0].activities[0].poi
    poi.evidence = poi.evidence.model_copy(update={"tool_call_id": None})
    ItineraryValidator().apply_status(verified_itinerary, validation_context)
    assert verified_itinerary.status == VerificationStatus.UNVERIFIED
    assert "UNVERIFIED_POI" in _codes(verified_itinerary.validation_issues)


def test_only_expired_evidence_produces_expired_status(
    verified_itinerary, validation_context
):
    route_evidence = verified_itinerary.days[0].route_legs[0].evidence
    assert route_evidence is not None and route_evidence.expires_at is not None
    validation_context.now = route_evidence.expires_at
    ItineraryValidator().apply_status(verified_itinerary, validation_context)
    assert verified_itinerary.status == VerificationStatus.EXPIRED
    assert "EXPIRED_ROUTE_EVIDENCE" in _codes(verified_itinerary.validation_issues)


def test_expired_plus_business_error_is_unverified(verified_itinerary, validation_context):
    route_evidence = verified_itinerary.days[0].route_legs[0].evidence
    assert route_evidence is not None and route_evidence.expires_at is not None
    validation_context.now = route_evidence.expires_at
    validation_context.approved_poi_ids.remove(
        verified_itinerary.days[0].activities[0].poi.poi_id
    )
    ItineraryValidator().apply_status(verified_itinerary, validation_context)
    assert verified_itinerary.status == VerificationStatus.UNVERIFIED
    assert {"EXPIRED_ROUTE_EVIDENCE", "UNKNOWN_POI"} <= _codes(
        verified_itinerary.validation_issues
    )


def test_empty_itinerary_is_rejected(verified_itinerary, validation_context):
    verified_itinerary.days.clear()
    ItineraryValidator().apply_status(verified_itinerary, validation_context)
    assert verified_itinerary.status == VerificationStatus.UNVERIFIED
    assert "EMPTY_ITINERARY" in _codes(verified_itinerary.validation_issues)


def test_activity_date_must_match_parent_day(verified_itinerary, validation_context):
    activity = verified_itinerary.days[0].activities[0]
    activity.day += timedelta(days=1)
    issues = ItineraryValidator().validate(verified_itinerary, validation_context)
    assert "ACTIVITY_DAY_MISMATCH" in _codes(issues)


def test_full_activity_must_fit_daily_window(verified_itinerary, validation_context):
    verified_itinerary.days[0].activities[-1].end_time = (
        verified_itinerary.request.daily_end_time.replace(hour=22)
    )
    issues = ItineraryValidator().validate(verified_itinerary, validation_context)
    assert "OUTSIDE_DAILY_WINDOW" in _codes(issues)


def test_missing_coordinates_are_rejected(verified_itinerary, validation_context):
    verified_itinerary.days[0].activities[0].poi.longitude = None
    issues = ItineraryValidator().validate(verified_itinerary, validation_context)
    assert "INVALID_POI_COORDINATES" in _codes(issues)


def test_duplicate_and_unknown_pois_are_rejected(verified_itinerary, validation_context):
    activities = verified_itinerary.days[0].activities
    activities[1].poi = activities[0].poi.model_copy(deep=True)
    validation_context.approved_poi_ids.clear()
    issues = ItineraryValidator().validate(verified_itinerary, validation_context)
    assert {"DUPLICATE_POI", "UNKNOWN_POI"} <= _codes(issues)


def test_route_must_match_exact_adjacent_endpoints(verified_itinerary, validation_context):
    verified_itinerary.days[0].route_legs[0].destination_activity_id = "not-adjacent"
    issues = ItineraryValidator().validate(verified_itinerary, validation_context)
    assert {"ROUTE_ENDPOINT_MISMATCH", "MISSING_ROUTE"} <= _codes(issues)


def test_route_requires_positive_distance_and_duration(
    verified_itinerary, validation_context
):
    route = verified_itinerary.days[0].route_legs[0]
    route.distance_meters = 0
    route.duration_minutes = None
    issues = ItineraryValidator().validate(verified_itinerary, validation_context)
    assert "INVALID_ROUTE_METRICS" in _codes(issues)


def test_route_requires_fifteen_minute_buffer(verified_itinerary, validation_context):
    verified_itinerary.days[0].activities[1].start_time = (
        verified_itinerary.days[0].activities[1].start_time.replace(hour=11, minute=40)
    )
    issues = ItineraryValidator().validate(verified_itinerary, validation_context)
    assert "INSUFFICIENT_ROUTE_BUFFER" in _codes(issues)


@pytest.mark.parametrize("change", ["time", "poi", "locked"])
def test_locked_activity_cannot_change(
    verified_itinerary, validation_context, change
):
    activity = verified_itinerary.days[0].activities[0]
    activity.locked = True
    snapshot = LockedActivitySnapshot.from_activity(activity)
    validation_context.locked_activities = [snapshot]
    if change == "time":
        activity.start_time = activity.start_time.replace(hour=10)
    elif change == "poi":
        activity.poi.poi_id = "CHANGED"
    else:
        activity.locked = False
    issues = ItineraryValidator().validate(verified_itinerary, validation_context)
    assert "LOCKED_ACTIVITY_CHANGED" in _codes(issues)


def test_missing_locked_activity_is_rejected(verified_itinerary, validation_context):
    activity = verified_itinerary.days[0].activities[0]
    activity.locked = True
    validation_context.locked_activities = [LockedActivitySnapshot.from_activity(activity)]
    verified_itinerary.days[0].activities.pop(0)
    issues = ItineraryValidator().validate(verified_itinerary, validation_context)
    assert "LOCKED_ACTIVITY_MISSING" in _codes(issues)


def test_warning_does_not_block_verified_status(verified_itinerary, validation_context):
    route = verified_itinerary.days[0].route_legs[0]
    route.transport_mode = "步行"
    route.distance_meters = 20_000
    ItineraryValidator().apply_status(verified_itinerary, validation_context)
    assert "WALK_LIMIT_EXCEEDED" in _codes(verified_itinerary.validation_issues)
    assert verified_itinerary.status == VerificationStatus.VERIFIED


def test_budget_exceeded_is_warning_not_error(verified_itinerary, validation_context):
    verified_itinerary.budget.accommodation_estimate = 20_000
    ItineraryValidator().apply_status(verified_itinerary, validation_context)
    issues = verified_itinerary.validation_issues
    assert "BUDGET_EXCEEDED" in _codes(issues)
    assert all(
        issue.severity == "warning"
        for issue in issues
        if issue.code == "BUDGET_EXCEEDED"
    )
    assert verified_itinerary.status == VerificationStatus.VERIFIED


def test_wrong_evidence_kind_is_rejected(verified_itinerary, validation_context):
    route = verified_itinerary.days[0].route_legs[0]
    assert route.evidence is not None
    route.evidence = route.evidence.model_copy(update={"kind": EvidenceKind.POI_LOCATION})
    issues = ItineraryValidator().validate(verified_itinerary, validation_context)
    assert "INVALID_ROUTE_EVIDENCE" in _codes(issues)


def test_upstream_unknown_poi_error_is_preserved(verified_itinerary, validation_context):
    verified_itinerary.validation_issues = [
        ValidationIssue(
            severity="error",
            code="unknown_poi",
            message="模型引用了批准列表之外的 POI，已移除",
        )
    ]
    ItineraryValidator().apply_status(verified_itinerary, validation_context)
    assert "unknown_poi" in _codes(verified_itinerary.validation_issues)
    assert verified_itinerary.status == VerificationStatus.UNVERIFIED
