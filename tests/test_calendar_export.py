from icalendar import Calendar

from travel_planner.services.calendar_export import generate_ics


def test_ics_contains_all_day_summary_and_timed_activities(verified_itinerary):
    payload = generate_ics(verified_itinerary)
    calendar = Calendar.from_ical(payload)
    events = [component for component in calendar.walk() if component.name == "VEVENT"]
    assert len(events) == 3
    all_day = events[0]
    assert (all_day.decoded("dtend") - all_day.decoded("dtstart")).days == 1
    assert events[1].decoded("dtstart").hour == 9

