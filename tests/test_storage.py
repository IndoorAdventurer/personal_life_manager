"""
Tests for the JsonStore storage layer (Chunk 5).

Each test gets its own empty temporary directory via pytest's built-in
`tmp_path` fixture, so tests are fully isolated — no shared state, no
manual cleanup required.

The `store` fixture wires JsonStore to that temp dir, giving us a
completely blank slate for every test.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from plm.models.inbox import InboxNote
from plm.models.planning import TimeBlock, WeeklyPlan
from plm.models.profile import BehavioralProfile
from plm.models.project import Project
from plm.storage.store import JsonStore


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path: Path) -> JsonStore:
    """Return a JsonStore backed by a fresh, empty temporary directory."""
    return JsonStore(data_dir=tmp_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(**kwargs) -> Project:
    """Minimal valid Project. Any field can be overridden via kwargs."""
    return Project(name=kwargs.pop("name", "Test Project"), **kwargs)


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

class TestInit:
    def test_creates_projects_subdir(self, tmp_path: Path):
        JsonStore(data_dir=tmp_path)
        assert (tmp_path / "projects").is_dir()

    def test_creates_planning_subdir(self, tmp_path: Path):
        JsonStore(data_dir=tmp_path)
        assert (tmp_path / "planning").is_dir()

    def test_accepts_path_object(self, tmp_path: Path):
        # data_dir should work as a plain Path, not just a string
        s = JsonStore(data_dir=tmp_path)
        assert s._root == tmp_path

    def test_accepts_string_path(self, tmp_path: Path):
        # The constructor wraps the arg in Path(), so strings are fine too
        s = JsonStore(data_dir=str(tmp_path))
        assert s._root == tmp_path


# ---------------------------------------------------------------------------
# Projects — save / get / delete
# ---------------------------------------------------------------------------

class TestProjectSaveGet:
    def test_round_trip(self, store: JsonStore):
        p = _make_project(name="My Project")
        store.save_project(p)
        loaded = store.get_project(p.id)
        assert loaded is not None
        assert loaded.name == "My Project"
        assert loaded.id == p.id

    def test_get_missing_returns_none(self, store: JsonStore):
        assert store.get_project("does-not-exist") is None

    def test_save_stamps_updated_at_on_disk(self, store: JsonStore):
        p = _make_project()
        # Force a known-old timestamp so there's no ambiguity — even if the
        # test runs faster than datetime resolution, this will definitely differ
        p.updated_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        store.save_project(p)
        loaded = store.get_project(p.id)
        assert loaded.updated_at > datetime(2000, 1, 1, tzinfo=timezone.utc)

    def test_save_mutates_in_memory_updated_at(self, store: JsonStore):
        # The caller's in-memory reference must stay in sync with what's on disk
        p = _make_project()
        p.updated_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        store.save_project(p)
        # p itself must have been mutated — not just the on-disk copy
        assert p.updated_at > datetime(2000, 1, 1, tzinfo=timezone.utc)

    def test_overwrite(self, store: JsonStore):
        p = _make_project(name="Original")
        store.save_project(p)
        p.name = "Updated"
        store.save_project(p)
        loaded = store.get_project(p.id)
        assert loaded.name == "Updated"

    def test_file_written_to_projects_subdir(self, store: JsonStore, tmp_path: Path):
        p = _make_project()
        store.save_project(p)
        expected = tmp_path / "projects" / f"{p.id}.json"
        assert expected.exists()

    def test_no_tmp_file_left_after_save(self, store: JsonStore, tmp_path: Path):
        # Atomic write must clean up the .tmp sibling
        p = _make_project()
        store.save_project(p)
        tmp_files = list((tmp_path / "projects").glob("*.tmp"))
        assert tmp_files == []


class TestProjectDelete:
    def test_delete_existing(self, store: JsonStore):
        p = _make_project()
        store.save_project(p)
        result = store.delete_project(p.id)
        assert result is True
        assert store.get_project(p.id) is None

    def test_delete_missing_returns_false(self, store: JsonStore):
        assert store.delete_project("ghost") is False

    def test_delete_is_idempotent(self, store: JsonStore):
        p = _make_project()
        store.save_project(p)
        store.delete_project(p.id)
        # Second delete of the same id must not raise
        assert store.delete_project(p.id) is False


# ---------------------------------------------------------------------------
# Projects — list_projects
# ---------------------------------------------------------------------------

class TestListProjects:
    def test_empty_store_returns_empty_list(self, store: JsonStore):
        assert store.list_projects() == []

    def test_returns_all_projects(self, store: JsonStore):
        p1 = _make_project(name="A")
        p2 = _make_project(name="B")
        store.save_project(p1)
        store.save_project(p2)
        ids = {p.id for p in store.list_projects()}
        assert ids == {p1.id, p2.id}

    def test_sorted_by_target_weekly_hours_desc(self, store: JsonStore):
        low = _make_project(name="Low", target_weekly_hours=2)
        high = _make_project(name="High", target_weekly_hours=20)
        mid = _make_project(name="Mid", target_weekly_hours=10)
        for p in (low, high, mid):
            store.save_project(p)
        names = [p.name for p in store.list_projects()]
        assert names == ["High", "Mid", "Low"]

    def test_no_target_sorts_last(self, store: JsonStore):
        has_target = _make_project(name="Targeted", target_weekly_hours=5)
        no_target = _make_project(name="No target", target_weekly_hours=None)
        store.save_project(has_target)
        store.save_project(no_target)
        names = [p.name for p in store.list_projects()]
        assert names[0] == "Targeted"
        assert names[-1] == "No target"

    def test_skips_corrupt_files(self, store: JsonStore, tmp_path: Path):
        # A corrupt JSON file must not crash list_projects(), but it must
        # emit a warning so the user knows a project file is unreadable.
        bad = tmp_path / "projects" / "corrupt.json"
        bad.write_text("not valid json", encoding="utf-8")
        p = _make_project(name="Good")
        store.save_project(p)
        with pytest.warns(UserWarning, match="corrupt.json"):
            result = store.list_projects()
        assert len(result) == 1
        assert result[0].name == "Good"


# ---------------------------------------------------------------------------
# Weekly plans
# ---------------------------------------------------------------------------

class TestWeeklyPlans:
    def test_get_missing_week_returns_none(self, store: JsonStore):
        assert store.get_plan("2026-W10") is None

    def test_round_trip(self, store: JsonStore):
        plan = WeeklyPlan(week="2026-W10")
        store.save_plan(plan)
        loaded = store.get_plan("2026-W10")
        assert loaded is not None
        assert loaded.week == "2026-W10"

    def test_plan_with_time_blocks(self, store: JsonStore):
        block = TimeBlock(project_id="proj-1", day="monday", start_time="09:00", end_time="12:00")
        plan = WeeklyPlan(week="2026-W11", time_blocks=[block])
        store.save_plan(plan)
        loaded = store.get_plan("2026-W11")
        assert len(loaded.time_blocks) == 1
        assert loaded.time_blocks[0].day == "monday"

    def test_overwrite_plan(self, store: JsonStore):
        plan = WeeklyPlan(week="2026-W12", constraints="Quiet week")
        store.save_plan(plan)
        plan.constraints = "Busy week"
        store.save_plan(plan)
        loaded = store.get_plan("2026-W12")
        assert loaded.constraints == "Busy week"

    def test_file_written_to_planning_subdir(self, store: JsonStore, tmp_path: Path):
        store.save_plan(WeeklyPlan(week="2026-W13"))
        assert (tmp_path / "planning" / "2026-W13.json").exists()

    def test_invalid_week_string_raises(self, store: JsonStore):
        # Path-traversal guard: any non "YYYY-Www" string must be rejected
        with pytest.raises(ValueError, match="Invalid ISO week string"):
            store.get_plan("../../../etc/passwd")

    def test_invalid_week_bad_format(self, store: JsonStore):
        with pytest.raises(ValueError, match="Invalid ISO week string"):
            store.get_plan("2026W10")  # missing dash

    def test_no_tmp_file_left_after_save(self, store: JsonStore, tmp_path: Path):
        store.save_plan(WeeklyPlan(week="2026-W14"))
        tmp_files = list((tmp_path / "planning").glob("*.tmp"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

class TestInbox:
    def test_empty_inbox_returns_empty_list(self, store: JsonStore):
        # No inbox.json yet — must return [] not raise
        assert store.get_inbox() == []

    def test_round_trip(self, store: JsonStore):
        note = InboxNote(content="Buy milk")
        store.save_inbox([note])
        loaded = store.get_inbox()
        assert len(loaded) == 1
        assert loaded[0].content == "Buy milk"

    def test_multiple_notes(self, store: JsonStore):
        notes = [InboxNote(content=f"Note {i}") for i in range(5)]
        store.save_inbox(notes)
        assert len(store.get_inbox()) == 5

    def test_overwrite_replaces_all(self, store: JsonStore):
        # The inbox is always saved whole — old notes must be gone
        store.save_inbox([InboxNote(content="Old")])
        store.save_inbox([InboxNote(content="New A"), InboxNote(content="New B")])
        loaded = store.get_inbox()
        assert len(loaded) == 2
        assert all(n.content.startswith("New") for n in loaded)

    def test_save_empty_inbox(self, store: JsonStore):
        store.save_inbox([InboxNote(content="To remove")])
        store.save_inbox([])
        assert store.get_inbox() == []

    def test_no_tmp_file_left_after_save(self, store: JsonStore, tmp_path: Path):
        store.save_inbox([InboxNote(content="x")])
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_add_inbox_note_appends(self, store: JsonStore):
        store.save_inbox([InboxNote(content="First")])
        store.add_inbox_note(InboxNote(content="Second"))
        loaded = store.get_inbox()
        assert len(loaded) == 2
        assert loaded[1].content == "Second"

    def test_add_inbox_note_to_empty_inbox(self, store: JsonStore):
        # Should work even when inbox.json doesn't exist yet
        store.add_inbox_note(InboxNote(content="Only note"))
        loaded = store.get_inbox()
        assert len(loaded) == 1
        assert loaded[0].content == "Only note"

    def test_add_inbox_note_preserves_existing(self, store: JsonStore):
        # Existing notes must not be lost when appending
        originals = [InboxNote(content=f"Note {i}") for i in range(3)]
        store.save_inbox(originals)
        store.add_inbox_note(InboxNote(content="New"))
        loaded = store.get_inbox()
        assert len(loaded) == 4
        assert {n.content for n in loaded} == {"Note 0", "Note 1", "Note 2", "New"}


# ---------------------------------------------------------------------------
# Behavioral profile
# ---------------------------------------------------------------------------

class TestProfile:
    def test_missing_profile_returns_default(self, store: JsonStore):
        # No profile.json yet — must return a default, not None or an error
        profile = store.get_profile()
        assert isinstance(profile, BehavioralProfile)
        # Default profile has empty content
        assert profile.content == ""

    def test_round_trip(self, store: JsonStore):
        profile = BehavioralProfile(content="Likes short feedback loops.")
        store.save_profile(profile)
        loaded = store.get_profile()
        assert loaded.content == "Likes short feedback loops."

    def test_overwrite(self, store: JsonStore):
        store.save_profile(BehavioralProfile(content="Old content"))
        store.save_profile(BehavioralProfile(content="New content"))
        assert store.get_profile().content == "New content"

    def test_no_tmp_file_left_after_save(self, store: JsonStore, tmp_path: Path):
        store.save_profile(BehavioralProfile(content="x"))
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []
