"""Tests for plm.ical — ICS rendering of weekly plans."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar

from plm.colors import PALETTE, project_css_color
from plm.ical import CALENDAR_NAME, build_ics
from plm.models.planning import TimeBlock, WeeklyPlan
from plm.models.project import Project

_AMS = ZoneInfo("Europe/Amsterdam")
_PAST = datetime(2000, 1, 1, tzinfo=timezone.utc)


@pytest.fixture()
def project() -> Project:
    return Project(name="Deep work")


def _plan(week: str, *blocks: TimeBlock) -> WeeklyPlan:
    return WeeklyPlan(week=week, time_blocks=list(blocks), updated_at=_PAST)


def _events(ics: bytes) -> list:
    return Calendar.from_ical(ics).walk("VEVENT")


class TestCalendar:
    def test_empty_is_valid_calendar(self) -> None:
        cal = Calendar.from_ical(build_ics([], {}, _AMS))
        assert str(cal["x-wr-calname"]) == CALENDAR_NAME
        assert cal.walk("VEVENT") == []

    def test_one_event_per_block_across_plans(self, project: Project) -> None:
        b1 = TimeBlock(project_id=project.id, day="monday", start_time="09:00", end_time="10:00")
        b2 = TimeBlock(project_id=project.id, day="friday", start_time="14:00", end_time="15:00")
        ics = build_ics([_plan("2026-W38", b1), _plan("2026-W39", b2)], {project.id: project}, _AMS)
        assert len(_events(ics)) == 2


class TestEvent:
    def _single(self, project: Project, **kw) -> tuple[TimeBlock, object]:
        fields = {"day": "monday", "start_time": "09:00", "end_time": "10:30"} | kw
        block = TimeBlock(project_id=project.id, **fields)
        [event] = _events(build_ics([_plan("2026-W39", block)], {project.id: project}, _AMS))
        return block, event

    def test_uid_is_block_id(self, project: Project) -> None:
        block, event = self._single(project)
        assert str(event["uid"]) == f"{block.id}@plm"

    def test_times_converted_to_utc(self, project: Project) -> None:
        # 2026-W39 Monday = Sep 21, CEST (UTC+2)
        _, event = self._single(project)
        assert event["dtstart"].dt == datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)
        assert event["dtend"].dt == datetime(2026, 9, 21, 8, 30, tzinfo=timezone.utc)

    def test_winter_time_offset(self, project: Project) -> None:
        # 2026-W50 Wednesday = Dec 9, CET (UTC+1)
        block = TimeBlock(project_id=project.id, day="wednesday", start_time="09:00", end_time="10:00")
        [event] = _events(build_ics([_plan("2026-W50", block)], {project.id: project}, _AMS))
        assert event["dtstart"].dt == datetime(2026, 12, 9, 8, 0, tzinfo=timezone.utc)

    def test_end_2400_rolls_to_next_midnight(self, project: Project) -> None:
        _, event = self._single(project, day="sunday", start_time="23:00", end_time="24:00")
        # Sunday Sep 27 24:00 CEST = Sunday 22:00 UTC
        assert event["dtend"].dt == datetime(2026, 9, 27, 22, 0, tzinfo=timezone.utc)

    def test_dtstamp_is_plan_updated_at(self, project: Project) -> None:
        # Stable between fetches — not "now"
        _, event = self._single(project)
        assert event["dtstamp"].dt == _PAST

    def test_summary_description_color(self, project: Project) -> None:
        _, event = self._single(project, notes="Chapter 3")
        assert str(event["summary"]) == "Deep work"
        assert str(event["description"]) == "Chapter 3"
        assert str(event["color"]) == project_css_color(project.id)

    def test_no_description_without_notes(self, project: Project) -> None:
        _, event = self._single(project)
        assert "description" not in event

    def test_deleted_project_name(self, project: Project) -> None:
        block = TimeBlock(project_id="gone", day="monday", start_time="09:00", end_time="10:00")
        [event] = _events(build_ics([_plan("2026-W39", block)], {}, _AMS))
        assert str(event["summary"]) == "(deleted project)"

    def test_alarms_at_start_and_end(self, project: Project) -> None:
        _, event = self._single(project)
        alarms = event.walk("VALARM")
        related = sorted(a["trigger"].params["RELATED"] for a in alarms)
        assert related == ["END", "START"]
        assert all(a["trigger"].dt == timedelta(0) for a in alarms)
        assert all(str(a["action"]) == "DISPLAY" for a in alarms)


def test_css_names_are_distinct() -> None:
    names = [css for _, css in PALETTE]
    assert len(set(names)) == len(names)
