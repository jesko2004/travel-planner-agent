from datetime import date

import pytest
from pydantic import ValidationError

from travel_planner.models import TripRequest


def test_trip_days_are_inclusive(trip_request):
    assert trip_request.days == 3


def test_rejects_reversed_dates():
    with pytest.raises(ValidationError):
        TripRequest(
            origin_city="上海",
            destination="北京",
            start_date=date(2026, 10, 5),
            end_date=date(2026, 10, 2),
            total_budget=5000,
        )


def test_rejects_inconsistent_child_ages(trip_request):
    payload = trip_request.model_dump()
    payload.update(children=2, child_ages=[8])
    with pytest.raises(ValidationError):
        TripRequest.model_validate(payload)

