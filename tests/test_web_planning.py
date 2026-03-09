"""
Tests for the planning web routes (sub-chunk 8c-1).

Strategy
--------
Same pattern as test_web_board: monkeypatched temp-dir store, authenticated
TestClient.  All POST routes return 303; tests assert the redirect and then
inspect the store (or GET the page) to verify state.

TODO: not deeply reviewed by Vincent — worth a closer read at some point.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import plm.web.app as app_module
from plm.models.planning import TimeBlock, WeeklyPlan
from plm.models.project import Project
from plm.storage.store import JsonStore

_TEST_PASSWORD = "test-password"
_WEEK = "2026-W10"
_PREV_WEEK = "2026-W09"
_NEXT_WEEK = "2026-W11"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path: Path) -> JsonStore:
    return JsonStore(data_dir=tmp_path)


@pytest.fixture
def client(store: JsonStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "_PLM_PASSWORD", _TEST_PASSWORD)
    with TestClient(app_module.app, raise_server_exceptions=True) as c:
        resp = c.post("/login", data={"password": _TEST_PASSWORD},
                      follow_redirects=True)
        assert resp.status_code == 200
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(store: JsonStore, name: str = "Alpha") -> Project:
    p = Project(name=name)
    store.save_project(p)
    return p


def _make_plan(store: JsonStore, week: str = _WEEK) -> WeeklyPlan:
    plan = WeeklyPlan(week=week)
    store.save_plan(plan)
    return plan


def _make_block(
    project_id: str,
    day: str = "monday",
    start: str = "09:00",
    end: str = "10:00",
) -> TimeBlock:
    return TimeBlock(project_id=project_id, day=day, start_time=start, end_time=end)


# ---------------------------------------------------------------------------
# GET /planning
# ---------------------------------------------------------------------------

class TestPlanningGet:
    def test_no_week_shows_current_week(self, client: TestClient) -> None:
        """No ?week param → page loads for the current ISO week."""
        resp = client.get("/planning")
        assert resp.status_code == 200
        assert app_module._current_week() in resp.text

    def test_explicit_week_shown(self, client: TestClient) -> None:
        """?week=2026-W10 → the week string appears in the page."""
        resp = client.get(f"/planning?week={_WEEK}")
        assert resp.status_code == 200
        assert _WEEK in resp.text

    def test_missing_plan_shows_empty_state(self, client: TestClient) -> None:
        """Week with no saved plan → all days show 'No blocks.' without erroring."""
        resp = client.get(f"/planning?week={_WEEK}")
        assert resp.status_code == 200
        assert "No blocks." in resp.text

    def test_existing_blocks_displayed(self, client: TestClient, store: JsonStore) -> None:
        """Blocks saved in the store are rendered in the day list."""
        p = _make_project(store)
        plan = _make_plan(store)
        plan.time_blocks.append(_make_block(p.id, "tuesday", "14:00", "15:30"))
        store.save_plan(plan)

        resp = client.get(f"/planning?week={_WEEK}")
        assert resp.status_code == 200
        assert "14:00" in resp.text
        assert "15:30" in resp.text
        assert p.name in resp.text

    def test_invalid_week_falls_back_to_current(self, client: TestClient) -> None:
        """A malformed ?week silently falls back to the current week."""
        resp = client.get("/planning?week=../../etc/passwd")
        assert resp.status_code == 200
        assert app_module._current_week() in resp.text

    def test_prev_next_links_present(self, client: TestClient) -> None:
        """Prev and Next navigation links contain the adjacent week strings."""
        resp = client.get(f"/planning?week={_WEEK}")
        assert resp.status_code == 200
        assert _PREV_WEEK in resp.text
        assert _NEXT_WEEK in resp.text

    def test_today_link_absent_on_current_week(self, client: TestClient) -> None:
        """'Today' button is hidden when already viewing the current week."""
        resp = client.get(f"/planning?week={app_module._current_week()}")
        assert resp.status_code == 200
        # The Today anchor only appears when week != current_week
        assert ">Today<" not in resp.text

    def test_today_link_present_on_other_week(self, client: TestClient) -> None:
        """'Today' button appears when viewing a week other than the current one."""
        resp = client.get(f"/planning?week={_WEEK}")
        assert resp.status_code == 200
        assert ">Today<" in resp.text

    def test_session_notes_preloaded(self, client: TestClient, store: JsonStore) -> None:
        """Existing session notes are pre-filled in the textarea."""
        plan = WeeklyPlan(week=_WEEK, session_notes="Focus on finishing X.")
        store.save_plan(plan)
        resp = client.get(f"/planning?week={_WEEK}")
        assert "Focus on finishing X." in resp.text

    def test_no_active_projects_hides_form(self, client: TestClient) -> None:
        """When there are no active projects the add-block form is replaced by a notice."""
        resp = client.get(f"/planning?week={_WEEK}")
        assert resp.status_code == 200
        assert "No active projects yet" in resp.text

    def test_active_project_appears_in_dropdown(self, client: TestClient, store: JsonStore) -> None:
        """An active project name appears in the add-block dropdown."""
        p = _make_project(store, "Beta Project")
        resp = client.get(f"/planning?week={_WEEK}")
        assert p.name in resp.text

    def test_archived_project_excluded_from_dropdown(
        self, client: TestClient, store: JsonStore
    ) -> None:
        """Archived projects must not appear in the add-block dropdown."""
        p = _make_project(store, "Old Project")
        p.archived = True
        store.save_project(p)
        resp = client.get(f"/planning?week={_WEEK}")
        # The project name should not appear in the dropdown options.
        # (It may still appear in block rows, but there are no blocks here.)
        assert "Old Project" not in resp.text

    def test_deleted_project_block_shown_gracefully(
        self, client: TestClient, store: JsonStore
    ) -> None:
        """A block referencing a since-deleted project shows a fallback label."""
        plan = _make_plan(store)
        # Use a project ID that doesn't exist in the store
        plan.time_blocks.append(
            TimeBlock(project_id="ghost-id", day="monday", start_time="08:00", end_time="09:00")
        )
        store.save_plan(plan)
        resp = client.get(f"/planning?week={_WEEK}")
        assert "deleted project" in resp.text


# ---------------------------------------------------------------------------
# POST /planning/blocks  (add block)
# ---------------------------------------------------------------------------

class TestAddBlock:
    def test_add_creates_plan_when_missing(
        self, client: TestClient, store: JsonStore
    ) -> None:
        """Adding a block for a week with no plan materialises a plan file."""
        p = _make_project(store)
        resp = client.post("/planning/blocks", data={
            "week": _WEEK, "project_id": p.id,
            "day": "monday", "start_time": "09:00", "end_time": "10:00",
        }, follow_redirects=False)
        assert resp.status_code == 303
        plan = store.get_plan(_WEEK)
        assert plan is not None
        assert len(plan.time_blocks) == 1
        assert plan.time_blocks[0].day == "monday"
        assert plan.time_blocks[0].start_time == "09:00"

    def test_add_appends_to_existing_plan(
        self, client: TestClient, store: JsonStore
    ) -> None:
        """Adding a block to an existing plan appends without overwriting."""
        p = _make_project(store)
        plan = _make_plan(store)
        plan.time_blocks.append(_make_block(p.id, "monday", "08:00", "09:00"))
        store.save_plan(plan)

        client.post("/planning/blocks", data={
            "week": _WEEK, "project_id": p.id,
            "day": "tuesday", "start_time": "10:00", "end_time": "11:00",
        }, follow_redirects=False)

        plan = store.get_plan(_WEEK)
        assert plan is not None
        assert len(plan.time_blocks) == 2

    def test_add_with_notes(self, client: TestClient, store: JsonStore) -> None:
        """Optional notes are stored on the block."""
        p = _make_project(store)
        client.post("/planning/blocks", data={
            "week": _WEEK, "project_id": p.id,
            "day": "wednesday", "start_time": "14:00", "end_time": "16:00",
            "notes": "deep work session",
        }, follow_redirects=False)
        plan = store.get_plan(_WEEK)
        assert plan is not None
        assert plan.time_blocks[0].notes == "deep work session"

    def test_invalid_week_rejected(self, client: TestClient, store: JsonStore) -> None:
        p = _make_project(store)
        resp = client.post("/planning/blocks", data={
            "week": "bad-week", "project_id": p.id,
            "day": "monday", "start_time": "09:00", "end_time": "10:00",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert "Invalid week" in resp.text

    def test_unknown_project_rejected(self, client: TestClient) -> None:
        resp = client.post("/planning/blocks", data={
            "week": _WEEK, "project_id": "no-such-id",
            "day": "monday", "start_time": "09:00", "end_time": "10:00",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert "Project not found" in resp.text

    def test_invalid_day_rejected(self, client: TestClient, store: JsonStore) -> None:
        p = _make_project(store)
        resp = client.post("/planning/blocks", data={
            "week": _WEEK, "project_id": p.id,
            "day": "funday", "start_time": "09:00", "end_time": "10:00",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert "Invalid day" in resp.text

    def test_end_before_start_rejected(self, client: TestClient, store: JsonStore) -> None:
        p = _make_project(store)
        resp = client.post("/planning/blocks", data={
            "week": _WEEK, "project_id": p.id,
            "day": "monday", "start_time": "11:00", "end_time": "09:00",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert "End time must be after" in resp.text

    def test_equal_times_rejected(self, client: TestClient, store: JsonStore) -> None:
        """start_time == end_time is not a valid block."""
        p = _make_project(store)
        resp = client.post("/planning/blocks", data={
            "week": _WEEK, "project_id": p.id,
            "day": "monday", "start_time": "10:00", "end_time": "10:00",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert "End time must be after" in resp.text

    def test_redirect_preserves_week(self, client: TestClient, store: JsonStore) -> None:
        """Successful add redirects back to the same week."""
        p = _make_project(store)
        resp = client.post("/planning/blocks", data={
            "week": _WEEK, "project_id": p.id,
            "day": "friday", "start_time": "13:00", "end_time": "14:00",
        }, follow_redirects=False)
        assert resp.status_code == 303
        assert _WEEK in resp.headers["location"]


# ---------------------------------------------------------------------------
# POST /planning/blocks/{block_id}/delete
# ---------------------------------------------------------------------------

class TestDeleteBlock:
    def test_delete_removes_block(self, client: TestClient, store: JsonStore) -> None:
        p = _make_project(store)
        plan = _make_plan(store)
        block = _make_block(p.id)
        plan.time_blocks.append(block)
        store.save_plan(plan)

        resp = client.post(
            f"/planning/blocks/{block.id}/delete",
            data={"week": _WEEK},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        plan = store.get_plan(_WEEK)
        assert plan is not None
        assert len(plan.time_blocks) == 0

    def test_delete_nonexistent_block_shows_error(
        self, client: TestClient, store: JsonStore
    ) -> None:
        _make_plan(store)
        resp = client.post(
            "/planning/blocks/no-such-block/delete",
            data={"week": _WEEK},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "not found" in resp.text

    def test_delete_no_plan_shows_error(self, client: TestClient) -> None:
        """Deleting from a week that has no plan shows an error, not a crash."""
        resp = client.post(
            "/planning/blocks/any-id/delete",
            data={"week": _WEEK},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "No plan found" in resp.text

    def test_delete_invalid_week_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/planning/blocks/any-id/delete",
            data={"week": "../../etc"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "Invalid week" in resp.text

    def test_delete_only_removes_target_block(
        self, client: TestClient, store: JsonStore
    ) -> None:
        """Deleting one block leaves others untouched."""
        p = _make_project(store)
        plan = _make_plan(store)
        b1 = _make_block(p.id, "monday", "09:00", "10:00")
        b2 = _make_block(p.id, "monday", "11:00", "12:00")
        plan.time_blocks.extend([b1, b2])
        store.save_plan(plan)

        client.post(f"/planning/blocks/{b1.id}/delete", data={"week": _WEEK})
        plan = store.get_plan(_WEEK)
        assert plan is not None
        assert len(plan.time_blocks) == 1
        assert plan.time_blocks[0].id == b2.id


# ---------------------------------------------------------------------------
# POST /planning/notes
# ---------------------------------------------------------------------------

class TestSavePlanNotes:
    def test_save_creates_plan_when_missing(
        self, client: TestClient, store: JsonStore
    ) -> None:
        """Saving notes materialises a plan file even with no blocks."""
        resp = client.post("/planning/notes", data={
            "week": _WEEK,
            "session_notes": "Focus on delivery.",
            "constraints": "Thursday blocked.",
        }, follow_redirects=False)
        assert resp.status_code == 303
        plan = store.get_plan(_WEEK)
        assert plan is not None
        assert plan.session_notes == "Focus on delivery."
        assert plan.constraints == "Thursday blocked."

    def test_save_updates_existing_plan(self, client: TestClient, store: JsonStore) -> None:
        """Saving notes overwrites old notes without touching blocks."""
        p = _make_project(store)
        plan = WeeklyPlan(week=_WEEK, session_notes="Old notes.")
        block = _make_block(p.id)
        plan.time_blocks.append(block)
        store.save_plan(plan)

        client.post("/planning/notes", data={
            "week": _WEEK,
            "session_notes": "New notes.",
            "constraints": "",
        }, follow_redirects=False)

        plan = store.get_plan(_WEEK)
        assert plan is not None
        assert plan.session_notes == "New notes."
        assert plan.constraints == ""
        # Block should still be there
        assert len(plan.time_blocks) == 1

    def test_save_shows_flash(self, client: TestClient) -> None:
        resp = client.post("/planning/notes", data={
            "week": _WEEK, "session_notes": "", "constraints": "",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert "Notes saved" in resp.text

    def test_save_invalid_week_rejected(self, client: TestClient) -> None:
        resp = client.post("/planning/notes", data={
            "week": "../etc/passwd", "session_notes": "x",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert "Invalid week" in resp.text

    def test_save_redirect_preserves_week(self, client: TestClient) -> None:
        resp = client.post("/planning/notes", data={
            "week": _WEEK, "session_notes": "", "constraints": "",
        }, follow_redirects=False)
        assert resp.status_code == 303
        assert _WEEK in resp.headers["location"]


# ---------------------------------------------------------------------------
# Week helper unit tests
# ---------------------------------------------------------------------------

class TestWeekHelpers:
    def test_current_week_format(self) -> None:
        w = app_module._current_week()
        assert app_module._WEEK_RE.match(w), f"Bad format: {w!r}"

    def test_week_offset_forward(self) -> None:
        assert app_module._week_offset("2026-W10", 1) == "2026-W11"

    def test_week_offset_backward(self) -> None:
        assert app_module._week_offset("2026-W10", -1) == "2026-W09"

    def test_week_offset_year_boundary(self) -> None:
        # Week 52 or 53 of 2025 → Week 1 of 2026
        w = app_module._week_offset("2025-W52", 1)
        assert w.startswith("2026-W")

    def test_validate_week_accepts_valid(self) -> None:
        assert app_module._validate_week("2026-W10")

    def test_validate_week_rejects_traversal(self) -> None:
        assert not app_module._validate_week("../../etc/passwd")
        assert not app_module._validate_week("2026W10")
        assert not app_module._validate_week("2026-10")

    def test_week_label_same_month(self) -> None:
        label = app_module._week_label("2026-W10")
        assert "Mar" in label
        assert "2026" in label

    def test_week_label_cross_month(self) -> None:
        # 2026-W14 spans Mar 30 – Apr 5
        label = app_module._week_label("2026-W14")
        assert "Mar" in label
        assert "Apr" in label
