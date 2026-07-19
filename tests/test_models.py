from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from travel_planner.models import (
    DEFAULT_EVIDENCE_TTLS,
    EvidenceKind,
    SourceEvidence,
    TripRequest,
    VerificationStatus,
)


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _request_payload(**updates):
    payload = {
        "origin_city": "上海",
        "destination": "北京",
        "start_date": date(2026, 10, 2),
        "end_date": date(2026, 10, 4),
        "adults": 2,
        "total_budget": 8_000,
    }
    payload.update(updates)
    return payload


def test_trip_days_are_inclusive(trip_request):
    assert trip_request.days == 3


def test_rejects_reversed_dates():
    with pytest.raises(ValidationError):
        TripRequest(**_request_payload(start_date=date(2026, 10, 5)))


def test_rejects_inconsistent_child_ages(trip_request):
    payload = trip_request.model_dump()
    payload.update(children=2, child_ages=[8])
    with pytest.raises(ValidationError):
        TripRequest.model_validate(payload)


def test_rejects_child_age_when_there_are_no_children():
    with pytest.raises(ValidationError, match="没有儿童"):
        TripRequest(**_request_payload(child_ages=[8]))


@pytest.mark.parametrize("age", [-1, 18])
def test_rejects_child_age_outside_v1_range(age):
    with pytest.raises(ValidationError):
        TripRequest(**_request_payload(children=1, child_ages=[age]))


def test_city_and_list_text_boundaries_are_enforced():
    assert TripRequest(**_request_payload(destination="北" * 64)).destination == "北" * 64
    with pytest.raises(ValidationError):
        TripRequest(**_request_payload(destination="北" * 65))
    with pytest.raises(ValidationError):
        TripRequest(**_request_payload(must_visit=["景点"] * 31))
    with pytest.raises(ValidationError):
        TripRequest(**_request_payload(must_visit=["景" * 81]))


def test_transport_budget_and_extra_field_boundaries_are_enforced():
    with pytest.raises(ValidationError):
        TripRequest(**_request_payload(intercity_transport="车" * 1001))
    with pytest.raises(ValidationError):
        TripRequest(**_request_payload(total_budget=1_000_001))
    with pytest.raises(ValidationError):
        TripRequest(**_request_payload(unknown_field=True))


@pytest.mark.parametrize("kind,ttl", list(DEFAULT_EVIDENCE_TTLS.items()))
def test_default_evidence_ttl_is_derived_in_beijing_time(kind, ttl):
    checked_at = datetime(2026, 7, 19, 9, 0, tzinfo=BEIJING_TZ)
    evidence = SourceEvidence(
        kind=kind,
        source="高德地图",
        tool_name="read_tool",
        checked_at=checked_at,
    )
    assert evidence.expires_at == checked_at + ttl
    assert evidence.checked_at.tzinfo == BEIJING_TZ


def test_provider_expiry_can_shorten_but_not_extend_default_ttl():
    checked_at = datetime(2026, 7, 19, 9, 0, tzinfo=BEIJING_TZ)
    shorter = checked_at + timedelta(minutes=30)
    longer = checked_at + timedelta(days=60)
    short_evidence = SourceEvidence(
        kind=EvidenceKind.POI_LOCATION,
        source="高德地图",
        tool_name="detail",
        checked_at=checked_at,
        expires_at=shorter,
    )
    capped_evidence = SourceEvidence(
        kind=EvidenceKind.POI_LOCATION,
        source="高德地图",
        tool_name="detail",
        checked_at=checked_at,
        expires_at=longer,
    )
    assert short_evidence.expires_at == shorter
    assert capped_evidence.expires_at == checked_at + timedelta(days=30)


def test_forecast_requires_supplier_expiry():
    with pytest.raises(ValidationError, match="天气预报"):
        SourceEvidence(
            kind=EvidenceKind.WEATHER_FORECAST,
            source="高德地图",
            tool_name="weather",
        )


def test_verified_claim_without_auditable_tool_record_is_downgraded(fixed_now):
    evidence = SourceEvidence(
        kind=EvidenceKind.ROUTE,
        source="高德地图",
        tool_name="route",
        checked_at=fixed_now,
        status=VerificationStatus.VERIFIED,
    )
    assert evidence.status == VerificationStatus.UNVERIFIED
    assert evidence.status_at(fixed_now) == VerificationStatus.UNVERIFIED


def test_exact_expiry_boundary_is_expired(fixed_now):
    evidence = SourceEvidence(
        kind=EvidenceKind.CURRENT_WEATHER,
        source="高德地图",
        tool_name="weather",
        tool_call_id="call-weather",
        raw_identifier="weather-beijing",
        checked_at=fixed_now,
        status=VerificationStatus.VERIFIED,
    )
    assert evidence.expires_at is not None
    assert evidence.status_at(evidence.expires_at - timedelta(microseconds=1)) == VerificationStatus.VERIFIED
    assert evidence.status_at(evidence.expires_at) == VerificationStatus.EXPIRED


def test_future_checked_at_cannot_be_currently_verified(fixed_now):
    evidence = SourceEvidence(
        kind=EvidenceKind.ROUTE,
        source="高德地图",
        tool_name="route_planning",
        tool_call_id="call-route-future",
        raw_identifier="route-future",
        checked_at=fixed_now + timedelta(minutes=1),
        status=VerificationStatus.VERIFIED,
    )
    assert evidence.status_at(fixed_now) == VerificationStatus.UNVERIFIED


def test_naive_legacy_evidence_time_is_interpreted_as_beijing_time():
    evidence = SourceEvidence(
        kind=EvidenceKind.ROUTE,
        source="高德地图",
        tool_name="route",
        checked_at=datetime(2026, 7, 19, 9, 0),
    )
    assert evidence.checked_at.utcoffset() == timedelta(hours=8)
