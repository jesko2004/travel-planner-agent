from __future__ import annotations

from datetime import datetime, timedelta

from icalendar import Calendar, Event

from travel_planner.models import Itinerary


def generate_ics(itinerary: Itinerary) -> bytes:
    calendar = Calendar()
    calendar.add("prodid", "-//Private China Travel Planner//")
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", itinerary.title)

    for day_plan in itinerary.days:
        summary = Event()
        summary.add("summary", day_plan.title)
        summary.add("description", itinerary.overview)
        summary.add("dtstart", day_plan.day)
        summary.add("dtend", day_plan.day + timedelta(days=1))
        summary.add("dtstamp", datetime.now())
        calendar.add_component(summary)

        for activity in day_plan.activities:
            event = Event()
            event.add("uid", f"{activity.activity_id}@private-travel-planner")
            event.add("summary", activity.poi.name)
            event.add("location", activity.poi.address)
            event.add("description", activity.notes or activity.poi.reservation_note)
            event.add("dtstart", datetime.combine(activity.day, activity.start_time))
            event.add("dtend", datetime.combine(activity.day, activity.end_time))
            event.add("dtstamp", datetime.now())
            calendar.add_component(event)
    return calendar.to_ical()

