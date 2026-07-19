from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from travel_planner.models import (
    Activity,
    DayPlan,
    EvidenceKind,
    Itinerary,
    PoiCandidate,
    RouteLeg,
    SourceEvidence,
    TripRequest,
    ValidationContext,
    VerificationStatus,
)


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 10, 1, 10, 0, tzinfo=BEIJING_TZ)


@pytest.fixture
def trip_request() -> TripRequest:
    return TripRequest(
        origin_city="上海",
        destination="北京",
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 4),
        adults=2,
        total_budget=8000,
        hotel_budget_min=400,
        hotel_budget_max=700,
    )


@pytest.fixture
def verified_poi(fixed_now) -> PoiCandidate:
    return PoiCandidate(
        poi_id="B000A83M61",
        name="示例景点",
        address="北京市示例地址",
        longitude=116.397,
        latitude=39.908,
        evidence=SourceEvidence(
            kind=EvidenceKind.POI_LOCATION,
            source="高德地图",
            tool_name="maps_search_detail",
            tool_call_id="call-poi-1",
            raw_identifier="B000A83M61",
            checked_at=fixed_now,
            status=VerificationStatus.VERIFIED,
        ),
    )


@pytest.fixture
def verified_itinerary(trip_request, verified_poi, fixed_now) -> Itinerary:
    second = verified_poi.model_copy(
        update={
            "poi_id": "B000SECOND",
            "name": "第二景点",
            "evidence": verified_poi.evidence.model_copy(
                update={
                    "tool_call_id": "call-poi-2",
                    "raw_identifier": "B000SECOND",
                }
            ),
        }
    )
    first_activity = Activity(
        day=trip_request.start_date,
        start_time=time(9, 0),
        end_time=time(11, 0),
        poi=verified_poi,
        estimated_cost=50,
    )
    second_activity = Activity(
        day=trip_request.start_date,
        start_time=time(12, 0),
        end_time=time(14, 0),
        poi=second,
        estimated_cost=100,
    )
    route = RouteLeg(
        origin_activity_id=first_activity.activity_id,
        destination_activity_id=second_activity.activity_id,
        transport_mode="公共交通",
        distance_meters=3000,
        duration_minutes=30,
        evidence=SourceEvidence(
            kind=EvidenceKind.ROUTE,
            source="高德地图",
            tool_name="route_planning",
            tool_call_id="call-route-1",
            raw_identifier="route-1",
            checked_at=fixed_now,
            status=VerificationStatus.VERIFIED,
        ),
    )
    return Itinerary(
        request=trip_request,
        title="北京三日行",
        overview="测试行程",
        days=[
            DayPlan(
                day=trip_request.start_date,
                title="第一天",
                activities=[first_activity, second_activity],
                route_legs=[route],
            )
        ],
    )


@pytest.fixture
def validation_context(verified_itinerary, fixed_now) -> ValidationContext:
    return ValidationContext(
        now=fixed_now,
        destination_confirmed=True,
        approved_poi_ids={
            activity.poi.poi_id
            for day_plan in verified_itinerary.days
            for activity in day_plan.activities
        },
        required_stages={
            "normalize_request": True,
            "research_destination": True,
            "create_draft": True,
            "enrich_routes": True,
            "validate": True,
        },
    )

