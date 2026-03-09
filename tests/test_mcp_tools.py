"""
Tests for the MCP server tool layer (Chunk 7).

Strategy
--------
Each test patches `plm.mcp_server.server.store` with a JsonStore backed by
pytest's `tmp_path`, then calls the tool functions directly as plain Python
functions.  No MCP protocol is involved — we're testing the business logic.

Fixture `srv_store` (used by every test class via `autouse=False`) swaps the
module-level singleton so that all tool calls hit an isolated temp directory.
After the test the original singleton is restored by the fixture's teardown.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from datetime import datetime, timezone

import plm.mcp_server.server as srv
from plm.storage.store import JsonStore

# Sentinel past datetime used in updated_at tests.  Setting a card's timestamp
# to a known value in the past ensures the "after" value will differ even when
# the code runs faster than the system clock's resolution.
_PAST = datetime(2000, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path: Path) -> JsonStore:
    """A blank JsonStore in a temp directory."""
    return JsonStore(data_dir=tmp_path)


@pytest.fixture(autouse=True)
def patch_store(store: JsonStore):
    """
    Swap the module-level store singleton before each test, restore after.

    autouse=True means every test in this module gets an isolated store
    automatically — no need to manually request the fixture.
    """
    original = srv.store
    srv.store = store
    yield store
    srv.store = original


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_project(name: str = "Test Project", **kwargs) -> str:
    """Create a project via the MCP tool and return its id."""
    result = srv.create_project(name=name, **kwargs)
    return result["project_id"]


def _first_column_id(project_id: str) -> str:
    """Return the id of the first column in a project."""
    cols = srv.list_columns(project_id)["columns"]
    return cols[0]["id"]


def _wip_column_id(project_id: str) -> str:
    """Return the id of the WIP column in a project."""
    cols = srv.list_columns(project_id)["columns"]
    wip = next(c for c in cols if c["is_wip"])
    return wip["id"]


def _add_card(project_id: str, column_id: str | None = None, name: str = "Card") -> str:
    """Add a card and return its id.  Defaults to the first column."""
    if column_id is None:
        column_id = _first_column_id(project_id)
    result = srv.add_card(project_id=project_id, column_id=column_id, name=name)
    return result["card_id"]


def _backdate_card(project_id: str, card_id: str) -> None:
    """
    Set a card's updated_at to _PAST so timestamp tests can assert it changed.

    Without backdating, the 'before' and 'after' snapshots can be identical
    when the test runs faster than the system clock's resolution.
    """
    project = srv.store.get_project(project_id)
    assert project is not None
    result = project.board.find_card(card_id)
    assert result is not None
    _col, card = result
    card.updated_at = _PAST
    srv.store.save_project(project)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class TestListProjects:
    def test_empty_initially(self):
        assert srv.list_projects()["projects"] == []

    def test_returns_created_project(self):
        pid = _create_project("Alpha")
        projects = srv.list_projects()["projects"]
        assert any(p["id"] == pid and p["name"] == "Alpha" for p in projects)

    def test_excludes_archived_by_default(self):
        pid = _create_project("Archived")
        srv.archive_project(pid)
        ids = [p["id"] for p in srv.list_projects()["projects"]]
        assert pid not in ids

    def test_includes_archived_when_requested(self):
        pid = _create_project("Archived")
        srv.archive_project(pid)
        ids = [p["id"] for p in srv.list_projects(include_archived=True)["projects"]]
        assert pid in ids

    def test_returned_fields_are_minimal(self):
        """list_projects should NOT return full board data."""
        _create_project("Alpha")
        p = srv.list_projects()["projects"][0]
        assert "board" not in p
        assert {"id", "name", "description", "target_weekly_hours", "archived"}.issubset(p)

    def test_no_warnings_key_when_no_corrupt_files(self):
        _create_project()
        result = srv.list_projects()
        assert "warnings" not in result


class TestCreateProject:
    def test_returns_ok_and_id(self):
        result = srv.create_project(name="Beta")
        assert result["ok"] is True
        assert "project_id" in result

    def test_persists_to_disk(self):
        pid = _create_project("Gamma")
        # Re-read directly from store to confirm persistence
        assert srv.store.get_project(pid) is not None

    def test_description_stored(self):
        pid = _create_project("Delta", description="desc here")
        p = srv.store.get_project(pid)
        assert p is not None
        assert p.description == "desc here"

    def test_target_weekly_hours_stored(self):
        pid = _create_project("Epsilon", target_weekly_hours=10.0)
        p = srv.store.get_project(pid)
        assert p is not None
        assert p.target_weekly_hours == 10.0


class TestGetProject:
    def test_returns_full_project(self):
        pid = _create_project("Zeta")
        result = srv.get_project(pid)
        assert result["id"] == pid
        assert "board" in result

    def test_raises_for_unknown_id(self):
        with pytest.raises(ValueError, match="not found"):
            srv.get_project("no-such-id")


class TestUpdateProject:
    def test_updates_name(self):
        pid = _create_project("Old Name")
        srv.update_project(project_id=pid, name="New Name")
        p = srv.store.get_project(pid)
        assert p is not None
        assert p.name == "New Name"

    def test_updates_description(self):
        pid = _create_project("Eta")
        srv.update_project(project_id=pid, description="updated desc")
        p = srv.store.get_project(pid)
        assert p is not None
        assert p.description == "updated desc"

    def test_updates_target_weekly_hours(self):
        pid = _create_project("Theta")
        srv.update_project(project_id=pid, target_weekly_hours=20.0)
        p = srv.store.get_project(pid)
        assert p is not None
        assert p.target_weekly_hours == 20.0

    def test_partial_update_leaves_other_fields(self):
        pid = _create_project("Iota", description="keep me", target_weekly_hours=5.0)
        srv.update_project(project_id=pid, name="Iota 2")
        p = srv.store.get_project(pid)
        assert p is not None
        assert p.description == "keep me"
        assert p.target_weekly_hours == 5.0

    def test_raises_for_unknown_id(self):
        with pytest.raises(ValueError, match="not found"):
            srv.update_project(project_id="bad-id", name="X")


class TestArchiveProject:
    def test_sets_archived_flag(self):
        pid = _create_project("Kappa")
        srv.archive_project(pid)
        p = srv.store.get_project(pid)
        assert p is not None
        assert p.archived is True

    def test_raises_for_unknown_id(self):
        with pytest.raises(ValueError, match="not found"):
            srv.archive_project("bad-id")


class TestGetWipOverview:
    def test_empty_when_no_wip_cards(self):
        _create_project("Lambda")
        result = srv.get_wip_overview()
        assert result["wip"] == []

    def test_returns_wip_cards(self):
        pid = _create_project("Mu")
        wip_col = _wip_column_id(pid)
        card_id = _add_card(pid, column_id=wip_col, name="In Flight")
        result = srv.get_wip_overview()
        assert len(result["wip"]) == 1
        assert result["wip"][0]["project_id"] == pid
        card_names = [c["name"] for c in result["wip"][0]["wip_cards"]]
        assert "In Flight" in card_names

    def test_excludes_archived_projects(self):
        pid = _create_project("Nu")
        wip_col = _wip_column_id(pid)
        _add_card(pid, column_id=wip_col)
        srv.archive_project(pid)
        result = srv.get_wip_overview()
        assert result["wip"] == []

    def test_result_contains_id_and_name_per_card(self):
        """Verify compact format: only id + name per card, no extra fields."""
        pid = _create_project("Xi")
        wip_col = _wip_column_id(pid)
        _add_card(pid, column_id=wip_col)
        card = srv.get_wip_overview()["wip"][0]["wip_cards"][0]
        assert set(card.keys()) == {"id", "name"}

    def test_collects_wip_cards_from_multiple_projects(self):
        """All active projects with WIP cards should appear in the overview."""
        pid_a = _create_project("Alpha")
        pid_b = _create_project("Beta")
        _add_card(pid_a, column_id=_wip_column_id(pid_a), name="Task A")
        _add_card(pid_b, column_id=_wip_column_id(pid_b), name="Task B")
        result = srv.get_wip_overview()["wip"]
        project_ids = {entry["project_id"] for entry in result}
        assert pid_a in project_ids
        assert pid_b in project_ids

    def test_shows_non_archived_wip_when_mixed(self):
        """With one archived and one active project, only the active one appears."""
        pid_archived = _create_project("Gone")
        pid_active = _create_project("Active")
        _add_card(pid_archived, column_id=_wip_column_id(pid_archived), name="Old")
        _add_card(pid_active, column_id=_wip_column_id(pid_active), name="Live")
        srv.archive_project(pid_archived)
        result = srv.get_wip_overview()["wip"]
        project_ids = {entry["project_id"] for entry in result}
        assert pid_archived not in project_ids
        assert pid_active in project_ids


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

class TestListColumns:
    def test_default_board_has_four_columns(self):
        pid = _create_project()
        cols = srv.list_columns(pid)["columns"]
        assert len(cols) == 4

    def test_returns_is_wip_flag(self):
        pid = _create_project()
        cols = srv.list_columns(pid)["columns"]
        wip_cols = [c for c in cols if c["is_wip"]]
        assert len(wip_cols) == 1

    def test_returns_card_count(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        _add_card(pid, column_id=col_id)
        _add_card(pid, column_id=col_id)
        cols = srv.list_columns(pid)["columns"]
        target = next(c for c in cols if c["id"] == col_id)
        others = [c for c in cols if c["id"] != col_id]
        assert target["card_count"] == 2
        # Cards were only added to one column — all others must still be empty
        assert all(c["card_count"] == 0 for c in others)

    def test_raises_for_unknown_project(self):
        with pytest.raises(ValueError, match="not found"):
            srv.list_columns("bad-id")


class TestAddColumn:
    def test_adds_column(self):
        pid = _create_project()
        before = len(srv.list_columns(pid)["columns"])
        srv.add_column(project_id=pid, name="Review")
        after = len(srv.list_columns(pid)["columns"])
        assert after == before + 1

    def test_returns_column_id(self):
        pid = _create_project()
        result = srv.add_column(project_id=pid, name="Review")
        assert "column_id" in result and result["ok"] is True

    def test_column_persisted(self):
        pid = _create_project()
        result = srv.add_column(project_id=pid, name="Review")
        col_id = result["column_id"]
        cols = srv.list_columns(pid)["columns"]
        assert any(c["id"] == col_id for c in cols)

    def test_position_respected(self):
        pid = _create_project()
        result = srv.add_column(project_id=pid, name="First", position=0)
        cols = srv.list_columns(pid)["columns"]
        assert cols[0]["id"] == result["column_id"]


class TestRenameColumn:
    def test_renames_column(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        srv.rename_column(project_id=pid, column_id=col_id, name="New Name")
        cols = srv.list_columns(pid)["columns"]
        col = next(c for c in cols if c["id"] == col_id)
        assert col["name"] == "New Name"

    def test_raises_for_unknown_column(self):
        pid = _create_project()
        with pytest.raises(ValueError):
            srv.rename_column(project_id=pid, column_id="bad-col", name="X")


class TestRemoveColumn:
    def test_removes_empty_column(self):
        pid = _create_project()
        result = srv.add_column(project_id=pid, name="Temp")
        col_id = result["column_id"]
        before = len(srv.list_columns(pid)["columns"])
        srv.remove_column(project_id=pid, column_id=col_id)
        remaining = srv.list_columns(pid)["columns"]
        # One fewer column overall, and specifically the right one is gone
        assert len(remaining) == before - 1
        assert not any(c["id"] == col_id for c in remaining)

    def test_refuses_non_empty_without_force(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        _add_card(pid, column_id=col_id)
        with pytest.raises(ValueError):
            srv.remove_column(project_id=pid, column_id=col_id, force=False)

    def test_force_removes_column_with_cards(self):
        pid = _create_project()
        result = srv.add_column(project_id=pid, name="Temp")
        col_id = result["column_id"]
        _add_card(pid, column_id=col_id)
        srv.remove_column(project_id=pid, column_id=col_id, force=True)
        cols = srv.list_columns(pid)["columns"]
        assert not any(c["id"] == col_id for c in cols)

    def test_refuses_to_remove_last_wip_column(self):
        """Cannot remove the only WIP column even with force=True."""
        pid = _create_project()
        wip_id = _wip_column_id(pid)
        # Remove all other columns first (they're empty)
        other_ids = [c["id"] for c in srv.list_columns(pid)["columns"] if c["id"] != wip_id]
        for col_id in other_ids:
            srv.remove_column(project_id=pid, column_id=col_id)
        with pytest.raises(ValueError):
            srv.remove_column(project_id=pid, column_id=wip_id, force=True)


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

class TestListCards:
    def test_empty_column(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        result = srv.list_cards(project_id=pid, column_id=col_id)
        assert result["cards"] == []

    def test_returns_added_card(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        card_id = _add_card(pid, column_id=col_id, name="My Card")
        cards = srv.list_cards(project_id=pid, column_id=col_id)["cards"]
        assert any(c["id"] == card_id and c["name"] == "My Card" for c in cards)

    def test_raises_for_unknown_column(self):
        pid = _create_project()
        with pytest.raises(ValueError, match="not found"):
            srv.list_cards(project_id=pid, column_id="bad-col")


class TestGetCard:
    def test_returns_card_details(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        card_id = _add_card(pid, column_id=col_id, name="Detail Card")
        card = srv.get_card(project_id=pid, card_id=card_id)
        assert card["id"] == card_id
        assert card["name"] == "Detail Card"
        assert "logs" in card

    def test_raises_for_unknown_card(self):
        pid = _create_project()
        with pytest.raises(ValueError, match="not found"):
            srv.get_card(project_id=pid, card_id="bad-card")


class TestAddCard:
    def test_returns_ok_and_card_id(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        result = srv.add_card(project_id=pid, column_id=col_id, name="New Card")
        assert result["ok"] is True
        assert "card_id" in result

    def test_stores_description(self):
        # Pass description directly to add_card — not via update_card
        pid = _create_project()
        col_id = _first_column_id(pid)
        result = srv.add_card(project_id=pid, column_id=col_id, name="X", description="my desc")
        card = srv.get_card(project_id=pid, card_id=result["card_id"])
        assert card["description"] == "my desc"

    def test_stores_estimated_workload(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        result = srv.add_card(
            project_id=pid, column_id=col_id, name="Heavy", estimated_workload="3h"
        )
        card = srv.get_card(project_id=pid, card_id=result["card_id"])
        assert card["estimated_workload"] == "3h"

    def test_raises_for_unknown_column(self):
        pid = _create_project()
        with pytest.raises(ValueError, match="not found"):
            srv.add_card(project_id=pid, column_id="bad-col", name="X")


class TestMoveCard:
    def test_moves_to_different_column(self):
        pid = _create_project()
        cols = srv.list_columns(pid)["columns"]
        src_id = cols[0]["id"]
        dst_id = cols[1]["id"]
        card_id = _add_card(pid, column_id=src_id)
        srv.move_card(project_id=pid, card_id=card_id, target_column_id=dst_id)
        # Card should now be in destination, not source
        src_cards = srv.list_cards(project_id=pid, column_id=src_id)["cards"]
        dst_cards = srv.list_cards(project_id=pid, column_id=dst_id)["cards"]
        assert not any(c["id"] == card_id for c in src_cards)
        assert any(c["id"] == card_id for c in dst_cards)

    def test_stamps_updated_at(self):
        pid = _create_project()
        cols = srv.list_columns(pid)["columns"]
        card_id = _add_card(pid, column_id=cols[0]["id"])
        # Backdate so the assertion can't accidentally pass due to clock resolution
        _backdate_card(pid, card_id)
        srv.move_card(project_id=pid, card_id=card_id, target_column_id=cols[1]["id"])
        after = srv.get_card(project_id=pid, card_id=card_id)["updated_at"]
        assert after > _PAST.isoformat()

    def test_raises_for_unknown_card(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        with pytest.raises(ValueError, match="not found"):
            srv.move_card(project_id=pid, card_id="bad", target_column_id=col_id)


class TestReorderCards:
    def test_reorders_cards(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        id_a = _add_card(pid, column_id=col_id, name="A")
        id_b = _add_card(pid, column_id=col_id, name="B")
        id_c = _add_card(pid, column_id=col_id, name="C")
        srv.reorder_cards(project_id=pid, column_id=col_id, card_ids=[id_c, id_a, id_b])
        cards = srv.list_cards(project_id=pid, column_id=col_id)["cards"]
        assert [c["id"] for c in cards] == [id_c, id_a, id_b]

    def test_raises_on_missing_card_id(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        _add_card(pid, column_id=col_id)
        with pytest.raises(ValueError):
            srv.reorder_cards(project_id=pid, column_id=col_id, card_ids=["wrong-id"])


class TestUpdateCard:
    def test_updates_name(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        card_id = _add_card(pid, column_id=col_id, name="Old")
        srv.update_card(project_id=pid, card_id=card_id, name="New")
        assert srv.get_card(project_id=pid, card_id=card_id)["name"] == "New"

    def test_partial_update_leaves_other_fields(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        result = srv.add_card(
            project_id=pid, column_id=col_id, name="Keep", estimated_workload="2h"
        )
        card_id = result["card_id"]
        srv.update_card(project_id=pid, card_id=card_id, description="new desc")
        card = srv.get_card(project_id=pid, card_id=card_id)
        assert card["name"] == "Keep"
        assert card["estimated_workload"] == "2h"

    def test_stamps_updated_at(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        card_id = _add_card(pid, column_id=col_id)
        # Backdate so the assertion can't accidentally pass due to clock resolution
        _backdate_card(pid, card_id)
        srv.update_card(project_id=pid, card_id=card_id, name="Changed")
        after = srv.get_card(project_id=pid, card_id=card_id)["updated_at"]
        assert after > _PAST.isoformat()

    def test_raises_for_unknown_card(self):
        pid = _create_project()
        with pytest.raises(ValueError, match="not found"):
            srv.update_card(project_id=pid, card_id="bad", name="X")


class TestAppendCardLog:
    def test_appends_log_entry(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        card_id = _add_card(pid, column_id=col_id)
        srv.append_card_log(project_id=pid, card_id=card_id, log_entry="Did a thing")
        card = srv.get_card(project_id=pid, card_id=card_id)
        assert len(card["logs"]) == 1
        assert card["logs"][0]["message"] == "Did a thing"

    def test_log_count_increments(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        card_id = _add_card(pid, column_id=col_id)
        r1 = srv.append_card_log(project_id=pid, card_id=card_id, log_entry="First")
        r2 = srv.append_card_log(project_id=pid, card_id=card_id, log_entry="Second")
        assert r1["log_count"] == 1
        assert r2["log_count"] == 2


class TestDeleteCard:
    def test_deletes_card(self):
        pid = _create_project()
        col_id = _first_column_id(pid)
        card_id = _add_card(pid, column_id=col_id)
        # Verify the card actually exists before we delete it
        assert any(c["id"] == card_id for c in srv.list_cards(project_id=pid, column_id=col_id)["cards"])
        srv.delete_card(project_id=pid, card_id=card_id)
        assert not any(c["id"] == card_id for c in srv.list_cards(project_id=pid, column_id=col_id)["cards"])

    def test_raises_for_unknown_card(self):
        pid = _create_project()
        with pytest.raises(ValueError, match="not found"):
            srv.delete_card(project_id=pid, card_id="bad-card")


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

class TestGetPlan:
    def test_returns_empty_structure_when_no_plan(self):
        result = srv.get_plan(week="2026-W01")
        assert result["week"] == "2026-W01"
        assert result["time_blocks"] == []

    def test_returns_existing_plan(self):
        srv.create_plan(week="2026-W10")
        result = srv.get_plan(week="2026-W10")
        assert result["week"] == "2026-W10"

    def test_blocks_sorted_by_day_and_time(self):
        """Blocks should come back monday→sunday, earliest start first."""
        pid = _create_project()
        # Add blocks out of order
        srv.add_time_block(week="2026-W10", project_id=pid, day="wednesday", start_time="09:00", end_time="10:00")
        srv.add_time_block(week="2026-W10", project_id=pid, day="monday", start_time="14:00", end_time="15:00")
        srv.add_time_block(week="2026-W10", project_id=pid, day="monday", start_time="09:00", end_time="10:00")
        blocks = srv.get_plan(week="2026-W10")["time_blocks"]
        days = [b["day"] for b in blocks]
        assert days == ["monday", "monday", "wednesday"]
        # monday blocks should be ordered by start_time
        monday_starts = [b["start_time"] for b in blocks if b["day"] == "monday"]
        assert monday_starts == ["09:00", "14:00"]


class TestCreatePlan:
    def test_creates_plan(self):
        result = srv.create_plan(week="2026-W05")
        assert result["ok"] is True
        assert srv.store.get_plan("2026-W05") is not None

    def test_overwrites_existing_plan(self):
        srv.create_plan(week="2026-W05", session_notes="old")
        srv.create_plan(week="2026-W05", session_notes="new")
        plan = srv.store.get_plan("2026-W05")
        assert plan is not None
        # The overwritten plan should have no blocks and the new notes
        assert plan.session_notes == "new"
        assert plan.time_blocks == []


class TestAddTimeBlock:
    def test_adds_block(self):
        pid = _create_project()
        result = srv.add_time_block(
            week="2026-W10", project_id=pid, day="monday",
            start_time="09:00", end_time="11:00"
        )
        assert result["ok"] is True
        assert "block_id" in result

    def test_auto_creates_plan(self):
        pid = _create_project()
        srv.add_time_block(
            week="2026-W11", project_id=pid, day="tuesday",
            start_time="10:00", end_time="12:00"
        )
        assert srv.store.get_plan("2026-W11") is not None

    def test_raises_for_invalid_day(self):
        pid = _create_project()
        with pytest.raises(ValueError, match="day must be one of"):
            srv.add_time_block(
                week="2026-W10", project_id=pid, day="funday",
                start_time="09:00", end_time="10:00"
            )

    def test_raises_when_end_before_start(self):
        pid = _create_project()
        with pytest.raises(ValueError, match="end_time.*must be after"):
            srv.add_time_block(
                week="2026-W10", project_id=pid, day="monday",
                start_time="10:00", end_time="09:00"
            )

    def test_raises_when_end_equals_start(self):
        pid = _create_project()
        with pytest.raises(ValueError, match="end_time.*must be after"):
            srv.add_time_block(
                week="2026-W10", project_id=pid, day="monday",
                start_time="10:00", end_time="10:00"
            )

    def test_raises_for_bad_time_format(self):
        pid = _create_project()
        with pytest.raises(ValueError, match="HH:MM"):
            srv.add_time_block(
                week="2026-W10", project_id=pid, day="monday",
                start_time="9am", end_time="10:00"
            )

    def test_raises_for_unknown_project(self):
        with pytest.raises(ValueError, match="not found"):
            srv.add_time_block(
                week="2026-W10", project_id="bad-id", day="monday",
                start_time="09:00", end_time="10:00"
            )


class TestRemoveTimeBlock:
    def test_removes_block(self):
        pid = _create_project()
        r = srv.add_time_block(
            week="2026-W10", project_id=pid, day="monday",
            start_time="09:00", end_time="10:00"
        )
        block_id = r["block_id"]
        srv.remove_time_block(week="2026-W10", block_id=block_id)
        blocks = srv.get_plan(week="2026-W10")["time_blocks"]
        assert not any(b["id"] == block_id for b in blocks)

    def test_raises_for_unknown_week(self):
        with pytest.raises(ValueError, match="No plan found"):
            srv.remove_time_block(week="2099-W99", block_id="x")

    def test_raises_for_unknown_block_id(self):
        srv.create_plan(week="2026-W10")
        with pytest.raises(ValueError, match="not found"):
            srv.remove_time_block(week="2026-W10", block_id="bad-block")


class TestUpdateTimeBlock:
    def _setup_block(self) -> tuple[str, str, str]:
        """Returns (project_id, week, block_id)."""
        pid = _create_project()
        week = "2026-W10"
        r = srv.add_time_block(
            week=week, project_id=pid, day="monday",
            start_time="09:00", end_time="11:00"
        )
        return pid, week, r["block_id"]

    def test_updates_notes(self):
        _, week, block_id = self._setup_block()
        srv.update_time_block(week=week, block_id=block_id, notes="Focus session")
        blocks = srv.get_plan(week=week)["time_blocks"]
        block = next(b for b in blocks if b["id"] == block_id)
        assert block["notes"] == "Focus session"

    def test_updates_day(self):
        _, week, block_id = self._setup_block()
        srv.update_time_block(week=week, block_id=block_id, day="friday")
        blocks = srv.get_plan(week=week)["time_blocks"]
        block = next(b for b in blocks if b["id"] == block_id)
        assert block["day"] == "friday"

    def test_updates_times(self):
        _, week, block_id = self._setup_block()
        srv.update_time_block(week=week, block_id=block_id, start_time="10:00", end_time="12:00")
        blocks = srv.get_plan(week=week)["time_blocks"]
        block = next(b for b in blocks if b["id"] == block_id)
        assert block["start_time"] == "10:00"
        assert block["end_time"] == "12:00"

    def test_raises_when_new_times_invalid(self):
        _, week, block_id = self._setup_block()
        # Push end_time before start_time
        with pytest.raises(ValueError, match="end_time.*must be after"):
            srv.update_time_block(week=week, block_id=block_id, end_time="08:00")

    def test_raises_for_unknown_week(self):
        with pytest.raises(ValueError, match="No plan found"):
            srv.update_time_block(week="2099-W99", block_id="x", notes="n")

    def test_raises_for_unknown_block(self):
        srv.create_plan(week="2026-W10")
        with pytest.raises(ValueError, match="not found"):
            srv.update_time_block(week="2026-W10", block_id="bad", notes="n")


class TestGetWeeklyHoursSummary:
    def test_empty_when_no_plan(self):
        result = srv.get_weekly_hours_summary(week="2026-W01")
        assert result["total_hours"] == 0.0
        assert result["by_project"] == []

    def test_calculates_hours(self):
        pid = _create_project("Alpha")
        # Two 2-hour blocks = 4 hours
        srv.add_time_block(
            week="2026-W10", project_id=pid, day="monday",
            start_time="09:00", end_time="11:00"
        )
        srv.add_time_block(
            week="2026-W10", project_id=pid, day="tuesday",
            start_time="14:00", end_time="16:00"
        )
        result = srv.get_weekly_hours_summary(week="2026-W10")
        assert result["total_hours"] == 4.0
        assert result["by_project"][0]["planned_hours"] == 4.0

    def test_multiple_projects(self):
        pid_a = _create_project("Alpha")
        pid_b = _create_project("Beta")
        srv.add_time_block(
            week="2026-W10", project_id=pid_a, day="monday",
            start_time="09:00", end_time="10:00"  # 1h
        )
        srv.add_time_block(
            week="2026-W10", project_id=pid_b, day="tuesday",
            start_time="10:00", end_time="13:00"  # 3h
        )
        result = srv.get_weekly_hours_summary(week="2026-W10")
        by_pid = {r["project_id"]: r["planned_hours"] for r in result["by_project"]}
        assert by_pid[pid_a] == 1.0
        assert by_pid[pid_b] == 3.0
        assert result["total_hours"] == 4.0

    def test_deleted_project_shown_as_placeholder(self):
        """Blocks referencing a deleted project should not crash — show placeholder name."""
        pid = _create_project("Temp")
        srv.add_time_block(
            week="2026-W10", project_id=pid, day="monday",
            start_time="09:00", end_time="10:00"
        )
        # Delete the project file directly so it's orphaned
        (srv.store._root / "projects" / f"{pid}.json").unlink()
        result = srv.get_weekly_hours_summary(week="2026-W10")
        name = result["by_project"][0]["project_name"]
        assert "deleted" in name.lower() or pid in name


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

class TestAddInboxNote:
    def test_returns_ok_and_note_id(self):
        result = srv.add_inbox_note(content="Remember this")
        assert result["ok"] is True
        assert "note_id" in result

    def test_note_stored(self):
        result = srv.add_inbox_note(content="Hello")
        note_id = result["note_id"]
        notes = srv.list_inbox_notes()["notes"]
        assert any(n["id"] == note_id for n in notes)


class TestListInboxNotes:
    def test_excludes_addressed_by_default(self):
        r = srv.add_inbox_note(content="Old note")
        srv.mark_inbox_note_addressed(r["note_id"])
        notes = srv.list_inbox_notes()["notes"]
        assert not any(n["id"] == r["note_id"] for n in notes)

    def test_includes_addressed_when_requested(self):
        r = srv.add_inbox_note(content="Old note")
        srv.mark_inbox_note_addressed(r["note_id"])
        notes = srv.list_inbox_notes(include_addressed=True)["notes"]
        assert any(n["id"] == r["note_id"] for n in notes)

    def test_multiple_unaddressed_notes(self):
        srv.add_inbox_note(content="A")
        srv.add_inbox_note(content="B")
        notes = srv.list_inbox_notes()["notes"]
        assert len(notes) == 2


class TestMarkInboxNoteAddressed:
    def test_marks_addressed(self):
        r = srv.add_inbox_note(content="Do later")
        srv.mark_inbox_note_addressed(r["note_id"])
        all_notes = srv.list_inbox_notes(include_addressed=True)["notes"]
        note = next(n for n in all_notes if n["id"] == r["note_id"])
        assert note["addressed"] is True

    def test_raises_for_unknown_note(self):
        with pytest.raises(ValueError, match="not found"):
            srv.mark_inbox_note_addressed("bad-id")


class TestDeleteInboxNote:
    def test_deletes_note(self):
        r = srv.add_inbox_note(content="Mistake")
        note_id = r["note_id"]
        srv.delete_inbox_note(note_id)
        all_notes = srv.list_inbox_notes(include_addressed=True)["notes"]
        assert not any(n["id"] == note_id for n in all_notes)

    def test_raises_for_unknown_note(self):
        with pytest.raises(ValueError, match="not found"):
            srv.delete_inbox_note("bad-id")


# ---------------------------------------------------------------------------
# Behavioral profile
# ---------------------------------------------------------------------------

class TestGetBehavioralProfile:
    def test_returns_empty_content_by_default(self):
        result = srv.get_behavioral_profile()
        assert result["content"] == ""
        assert result["last_updated"] is None

    def test_returns_updated_content(self):
        srv.update_behavioral_profile(content="I work best in the morning.", summary="Initial")
        result = srv.get_behavioral_profile()
        assert result["content"] == "I work best in the morning."

    def test_does_not_include_history(self):
        """History is intentionally omitted to keep context lean."""
        srv.update_behavioral_profile(content="X", summary="s")
        result = srv.get_behavioral_profile()
        assert "history" not in result


class TestGetProfileHistory:
    def test_empty_initially(self):
        result = srv.get_profile_history()
        assert result["history"] == []

    def test_records_update_summaries(self):
        srv.update_behavioral_profile(content="v1", summary="First write")
        srv.update_behavioral_profile(content="v2", summary="Second write")
        history = srv.get_profile_history()["history"]
        assert len(history) == 2
        assert history[0]["summary"] == "First write"
        assert history[1]["summary"] == "Second write"

    def test_each_entry_has_date_and_summary(self):
        srv.update_behavioral_profile(content="x", summary="entry")
        entry = srv.get_profile_history()["history"][0]
        assert "date" in entry and "summary" in entry


class TestUpdateBehavioralProfile:
    def test_replaces_content(self):
        srv.update_behavioral_profile(content="old", summary="s")
        srv.update_behavioral_profile(content="new", summary="s2")
        assert srv.get_behavioral_profile()["content"] == "new"

    def test_increments_history(self):
        r1 = srv.update_behavioral_profile(content="v1", summary="s1")
        r2 = srv.update_behavioral_profile(content="v2", summary="s2")
        assert r1["history_entries"] == 1
        assert r2["history_entries"] == 2

    def test_stamps_last_updated(self):
        srv.update_behavioral_profile(content="x", summary="s")
        assert srv.get_behavioral_profile()["last_updated"] is not None


class TestPatchBehavioralProfile:
    def test_replaces_substring(self):
        srv.update_behavioral_profile(content="I like mornings. I dislike noise.", summary="init")
        srv.patch_behavioral_profile(
            old_text="I dislike noise.",
            new_text="I dislike interruptions.",
            summary="refined"
        )
        assert srv.get_behavioral_profile()["content"] == "I like mornings. I dislike interruptions."

    def test_appends_history_entry(self):
        srv.update_behavioral_profile(content="Hello world", summary="init")
        srv.patch_behavioral_profile(old_text="world", new_text="there", summary="patch")
        history = srv.get_profile_history()["history"]
        assert history[-1]["summary"] == "patch"

    def test_raises_when_old_text_not_found(self):
        srv.update_behavioral_profile(content="Hello world", summary="init")
        with pytest.raises(ValueError, match="not found"):
            srv.patch_behavioral_profile(old_text="goodbye", new_text="hi", summary="s")

    def test_raises_when_old_text_ambiguous(self):
        srv.update_behavioral_profile(content="foo foo", summary="init")
        with pytest.raises(ValueError, match="matches 2 times"):
            srv.patch_behavioral_profile(old_text="foo", new_text="bar", summary="s")

    def test_can_delete_text_with_empty_new_text(self):
        srv.update_behavioral_profile(content="Keep this. Remove this.", summary="init")
        srv.patch_behavioral_profile(old_text=" Remove this.", new_text="", summary="trim")
        assert srv.get_behavioral_profile()["content"] == "Keep this."


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class TestGetWeeklyReviewData:
    def test_returns_all_keys(self):
        result = srv.get_weekly_review_data(week="2026-W10")
        assert {"week", "plan", "wip", "inbox", "profile"}.issubset(result)

    def test_empty_plan_when_no_plan_exists(self):
        result = srv.get_weekly_review_data(week="2026-W01")
        assert result["plan"]["time_blocks"] == []

    def test_includes_wip_cards(self):
        pid = _create_project("Review Project")
        wip_col = _wip_column_id(pid)
        card_id = _add_card(pid, column_id=wip_col, name="Active Task")
        result = srv.get_weekly_review_data(week="2026-W10")
        all_wip_card_ids = [
            c["id"]
            for entry in result["wip"]
            for c in entry["wip_cards"]
        ]
        assert card_id in all_wip_card_ids

    def test_includes_unaddressed_inbox_notes(self):
        r = srv.add_inbox_note(content="Think about this")
        result = srv.get_weekly_review_data(week="2026-W10")
        note_ids = [n["id"] for n in result["inbox"]]
        assert r["note_id"] in note_ids

    def test_excludes_addressed_inbox_notes(self):
        r = srv.add_inbox_note(content="Done with this")
        srv.mark_inbox_note_addressed(r["note_id"])
        result = srv.get_weekly_review_data(week="2026-W10")
        note_ids = [n["id"] for n in result["inbox"]]
        assert r["note_id"] not in note_ids

    def test_includes_profile_content(self):
        srv.update_behavioral_profile(content="My patterns.", summary="init")
        result = srv.get_weekly_review_data(week="2026-W10")
        assert result["profile"]["content"] == "My patterns."

    def test_plan_blocks_sorted(self):
        """Blocks in the review data should be sorted, same as get_plan()."""
        pid = _create_project()
        srv.add_time_block(week="2026-W10", project_id=pid, day="friday", start_time="09:00", end_time="10:00")
        srv.add_time_block(week="2026-W10", project_id=pid, day="monday", start_time="09:00", end_time="10:00")
        result = srv.get_weekly_review_data(week="2026-W10")
        days = [b["day"] for b in result["plan"]["time_blocks"]]
        assert days == ["monday", "friday"]
