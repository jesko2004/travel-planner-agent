from __future__ import annotations

from datetime import date, time

import pytest

from travel_planner.models import (
    Activity,
    DayPlan,
    Itinerary,
    PoiCandidate,
    RouteLeg,
    SourceEvidence,
    TripRequest,
    VerificationStatus,
)


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
def verified_poi() -> PoiCandidate:
    return PoiCandidate(
        poi_id="B000A83M61",
        name="示例景点",
        address="北京市示例地址",
        longitude=116.397,
        latitude=39.908,
        evidence=SourceEvidence(
            source="高德地图",
            tool_name="maps_search_detail",
            status=VerificationStatus.VERIFIED,
        ),
    )


@pytest.fixture
def verified_itinerary(trip_request, verified_poi) -> Itinerary:
    second = verified_poi.model_copy(update={"poi_id": "B000SECOND", "name": "第二景点"})
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
            source="高德地图",
            tool_name="route_planning",
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

