from travel_planner.models import VerificationStatus
from travel_planner.services.validator import ItineraryValidator


def test_verified_itinerary_has_no_errors(verified_itinerary):
    validator = ItineraryValidator()
    validator.apply_status(verified_itinerary, amap_available=True)
    assert verified_itinerary.status == VerificationStatus.VERIFIED
    assert not [issue for issue in verified_itinerary.validation_issues if issue.severity == "error"]
    assert verified_itinerary.budget.activities == 150


def test_missing_route_prevents_verified_status(verified_itinerary):
    verified_itinerary.days[0].route_legs.clear()
    validator = ItineraryValidator()
    validator.apply_status(verified_itinerary, amap_available=True)
    assert verified_itinerary.status == VerificationStatus.UNVERIFIED
    assert "MISSING_ROUTE" in {issue.code for issue in verified_itinerary.validation_issues}


def test_time_overlap_is_detected(verified_itinerary):
    verified_itinerary.days[0].activities[1].start_time = verified_itinerary.days[0].activities[0].start_time
    issues = ItineraryValidator().validate(verified_itinerary)
    assert "TIME_OVERLAP" in {issue.code for issue in issues}

