"""ICS (iCalendar) export of weekly plans — one subscribable feed for all blocks.

Pure functions only: callers pick which plans to include and pass them in, so
this module has no store or HTTP concerns and is easy to test.

Design notes:
  - UID is the block id, so a calendar app updates an edited/moved block in
    place instead of showing a duplicate.
  - Times are converted from local wall-clock (PLM_TIMEZONE) to UTC and written
    with a "Z" suffix.  That avoids having to emit VTIMEZONE definitions, which
    RFC 5545 requires for any TZID parameter.
  - Each event carries two VALARMs: one at the start and one at the end
    (RELATED=END), so the phone buzzes when a block begins and when it's over.
  - COLOR (RFC 7986) is a hint; many clients (Google) ignore it per event.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from icalendar import Alarm, Calendar, Event

from plm.colors import project_css_color
from plm.models.planning import TimeBlock, WeeklyPlan
from plm.models.project import Project
from plm.timeutil import local_tz

_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Shown as the calendar's name when subscribing (X-WR-CALNAME for Google/Apple,
# NAME for RFC 7986 clients).
CALENDAR_NAME = "PLM"


def _local_datetime(week: str, day: str, hhmm: str, tz: ZoneInfo) -> datetime:
    """Wall-clock time of *hhmm* on *day* of ISO *week*, as an aware datetime.

    Minutes are added as a timedelta (not via time(h, m)) so an end time of
    "24:00" still works and rolls over to the next midnight.
    """
    monday = datetime.strptime(f"{week}-1", "%G-W%V-%u")
    h, m = map(int, hhmm.split(":"))
    naive = monday + timedelta(days=_DAYS.index(day), hours=h, minutes=m)
    return naive.replace(tzinfo=tz)


def _alarm(description: str, trigger_related: str) -> Alarm:
    """DISPLAY alarm firing exactly at the event's START or END."""
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    # DESCRIPTION is mandatory for DISPLAY alarms (RFC 5545 § 3.6.6)
    alarm.add("description", description)
    alarm.add("trigger", timedelta(0), parameters={"RELATED": trigger_related})
    return alarm


def _block_event(
    plan: WeeklyPlan, block: TimeBlock, project: Project | None, tz: ZoneInfo,
) -> Event:
    name = project.name if project else "(deleted project)"
    event = Event()
    event.add("uid", f"{block.id}@plm")
    # DTSTAMP must be stable between fetches, otherwise clients may treat every
    # refresh as a change — use the plan's last modification, not "now".
    event.add("dtstamp", plan.updated_at.astimezone(timezone.utc))
    event.add("dtstart", _local_datetime(plan.week, block.day, block.start_time, tz).astimezone(timezone.utc))
    event.add("dtend", _local_datetime(plan.week, block.day, block.end_time, tz).astimezone(timezone.utc))
    event.add("summary", name)
    if block.notes:
        event.add("description", block.notes)
    event.add("color", project_css_color(block.project_id))
    event.add_component(_alarm(f"Start: {name}", "START"))
    event.add_component(_alarm(f"End: {name}", "END"))
    return event


def build_ics(
    plans: list[WeeklyPlan],
    projects: dict[str, Project],
    tz: ZoneInfo | None = None,
) -> bytes:
    """Render every time block in *plans* as one VCALENDAR.

    *projects* maps project id → Project (include archived ones so their blocks
    keep their name).  *tz* defaults to PLM_TIMEZONE.
    """
    tz = tz or local_tz()
    cal = Calendar()
    cal.add("prodid", "-//Personal Life Manager//PLM//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", CALENDAR_NAME)
    cal.add("name", CALENDAR_NAME)
    # Refresh hints — honoured by Apple/Thunderbird/ICSx⁵; Google ignores them.
    cal.add("refresh-interval", timedelta(hours=1), parameters={"VALUE": "DURATION"})
    cal.add("x-published-ttl", "PT1H")

    for plan in plans:
        for block in plan.time_blocks:
            cal.add_component(_block_event(plan, block, projects.get(block.project_id), tz))
    return cal.to_ical()
