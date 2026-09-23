"""Tests for the /calendar.ics feed route."""

from __future__ import annotations

from pathlib import Path

import pytest
from icalendar import Calendar
from starlette.testclient import TestClient

import plm.web.app as app_module
from plm.models.planning import TimeBlock, WeeklyPlan
from plm.models.project import Project
from plm.storage.store import JsonStore
from plm.timeutil import current_week

_TOKEN = "feed-secret"


@pytest.fixture
def store(tmp_path: Path) -> JsonStore:
    return JsonStore(data_dir=tmp_path)


@pytest.fixture
def client(store: JsonStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Deliberately NOT logged in — calendar apps have no session cookie.
    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "_PLM_ICAL_TOKEN", _TOKEN)
    with TestClient(app_module.app) as c:
        yield c


def _add_block(store: JsonStore, week: str, project: Project) -> TimeBlock:
    block = TimeBlock(project_id=project.id, day="monday", start_time="09:00", end_time="10:00")
    store.save_plan(WeeklyPlan(week=week, time_blocks=[block]))
    return block


def _uids(resp) -> set[str]:
    return {str(e["uid"]) for e in Calendar.from_ical(resp.content).walk("VEVENT")}


class TestAuth:
    def test_valid_token(self, client: TestClient) -> None:
        resp = client.get(f"/calendar.ics?token={_TOKEN}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/calendar")

    def test_wrong_token_404(self, client: TestClient) -> None:
        assert client.get("/calendar.ics?token=nope").status_code == 404

    def test_missing_token_404(self, client: TestClient) -> None:
        assert client.get("/calendar.ics").status_code == 404

    def test_disabled_when_token_unset(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An empty token must not match an empty ?token= and expose the feed
        monkeypatch.setattr(app_module, "_PLM_ICAL_TOKEN", "")
        assert client.get("/calendar.ics?token=").status_code == 404


class TestRange:
    def test_includes_last_week_onwards(self, client: TestClient, store: JsonStore) -> None:
        project = Project(name="P")
        store.save_project(project)
        this = current_week()
        old = _add_block(store, app_module._week_offset(this, -2), project)
        last = _add_block(store, app_module._week_offset(this, -1), project)
        now = _add_block(store, this, project)
        future = _add_block(store, app_module._week_offset(this, 5), project)

        uids = _uids(client.get(f"/calendar.ics?token={_TOKEN}"))
        assert uids == {f"{b.id}@plm" for b in (last, now, future)}
        assert f"{old.id}@plm" not in uids

    def test_archived_project_keeps_name(self, client: TestClient, store: JsonStore) -> None:
        project = Project(name="Old hobby", archived=True)
        store.save_project(project)
        _add_block(store, current_week(), project)
        resp = client.get(f"/calendar.ics?token={_TOKEN}")
        [event] = Calendar.from_ical(resp.content).walk("VEVENT")
        assert str(event["summary"]) == "Old hobby"
