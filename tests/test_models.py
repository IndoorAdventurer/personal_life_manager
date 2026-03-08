"""
Tests for all Pydantic data models (Chunk 3).

Each test class focuses on one model or a closely related pair of models.
We test defaults, validators, and the business-logic methods on KanbanBoard.
"""

import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from plm.models.card import CardLog, KanbanCard
from plm.models.column import KanbanColumn
from plm.models.board import KanbanBoard
from plm.models.project import Project
from plm.models.planning import TimeBlock, WeeklyPlan
from plm.models.inbox import InboxNote
from plm.models.profile import BehavioralProfile, ProfileUpdate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wip_board(*extra_columns: KanbanColumn) -> KanbanBoard:
    """Minimal valid board: one WIP column plus any extras passed in."""
    return KanbanBoard(
        columns=[KanbanColumn(name="In Progress", is_wip=True), *extra_columns]
    )


# ---------------------------------------------------------------------------
# CardLog
# ---------------------------------------------------------------------------

class TestCardLog:
    def test_timestamp_defaults_to_utc_now(self):
        before = datetime.now(timezone.utc)
        log = CardLog(message="started")
        after = datetime.now(timezone.utc)
        assert log.message == "started"
        assert before <= log.timestamp <= after

    def test_timestamp_is_timezone_aware(self):
        log = CardLog(message="x")
        assert log.timestamp.tzinfo is not None

    def test_custom_timestamp_accepted(self):
        ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        log = CardLog(timestamp=ts, message="manual")
        assert log.timestamp == ts


# ---------------------------------------------------------------------------
# KanbanCard
# ---------------------------------------------------------------------------

class TestKanbanCard:
    def test_minimal_card_has_defaults(self):
        card = KanbanCard(name="Fix bug")
        assert card.name == "Fix bug"
        assert card.description == ""
        assert card.logs == []
        assert card.estimated_workload is None
        assert card.id  # non-empty string

    def test_id_is_unique_per_instance(self):
        a = KanbanCard(name="A")
        b = KanbanCard(name="B")
        assert a.id != b.id

    def test_timestamps_are_timezone_aware(self):
        card = KanbanCard(name="T")
        assert card.created_at.tzinfo is not None
        assert card.updated_at.tzinfo is not None

    def test_logs_accept_card_log_objects(self):
        log = CardLog(message="progress note")
        card = KanbanCard(name="C", logs=[log])
        assert len(card.logs) == 1
        assert card.logs[0].message == "progress note"

    def test_estimated_workload_is_free_text(self):
        card = KanbanCard(name="D", estimated_workload="2h")
        assert card.estimated_workload == "2h"


# ---------------------------------------------------------------------------
# KanbanColumn
# ---------------------------------------------------------------------------

class TestKanbanColumn:
    def test_defaults(self):
        col = KanbanColumn(name="Backlog")
        assert col.name == "Backlog"
        assert col.description == ""
        assert col.is_wip is False
        assert col.cards == []
        assert col.id  # non-empty

    def test_wip_flag_can_be_set(self):
        col = KanbanColumn(name="Active", is_wip=True)
        assert col.is_wip is True

    def test_id_is_unique_per_instance(self):
        a = KanbanColumn(name="A")
        b = KanbanColumn(name="B")
        assert a.id != b.id


# ---------------------------------------------------------------------------
# KanbanBoard — WIP invariant
# ---------------------------------------------------------------------------

class TestKanbanBoardWIPInvariant:
    def test_empty_columns_list_is_valid(self):
        # An empty board is allowed; the invariant only applies when columns exist
        board = KanbanBoard(columns=[])
        assert board.columns == []

    def test_board_with_one_wip_column_is_valid(self):
        board = _wip_board()
        assert len(board.columns) == 1

    def test_board_without_wip_column_raises(self):
        with pytest.raises(ValidationError, match="WIP"):
            KanbanBoard(
                columns=[
                    KanbanColumn(name="Todo"),
                    KanbanColumn(name="Done"),
                ]
            )

    def test_multiple_wip_columns_are_allowed(self):
        # The invariant requires *at least one* WIP column, not exactly one
        board = KanbanBoard(
            columns=[
                KanbanColumn(name="WIP-A", is_wip=True),
                KanbanColumn(name="WIP-B", is_wip=True),
            ]
        )
        assert sum(c.is_wip for c in board.columns) == 2


# ---------------------------------------------------------------------------
# KanbanBoard — find_card / get_wip_cards
# ---------------------------------------------------------------------------

class TestKanbanBoardQueries:
    def setup_method(self):
        self.card_a = KanbanCard(name="A")
        self.card_b = KanbanCard(name="B")
        self.wip_col = KanbanColumn(name="In Progress", is_wip=True, cards=[self.card_a])
        self.done_col = KanbanColumn(name="Done", is_wip=False, cards=[self.card_b])
        self.board = KanbanBoard(columns=[self.wip_col, self.done_col])

    def test_find_card_returns_column_and_card(self):
        result = self.board.find_card(self.card_a.id)
        assert result is not None
        col, card = result
        assert col.id == self.wip_col.id
        assert card.id == self.card_a.id

    def test_find_card_returns_none_for_missing_id(self):
        assert self.board.find_card("nonexistent") is None

    def test_get_wip_cards_returns_only_wip_cards(self):
        wip_cards = self.board.get_wip_cards()
        assert len(wip_cards) == 1
        assert wip_cards[0].id == self.card_a.id


# ---------------------------------------------------------------------------
# KanbanBoard — move_card
# ---------------------------------------------------------------------------

class TestKanbanBoardMoveCard:
    def setup_method(self):
        self.card = KanbanCard(name="Task")
        self.src = KanbanColumn(name="Todo", cards=[self.card])
        self.wip = KanbanColumn(name="In Progress", is_wip=True)
        self.done = KanbanColumn(name="Done")
        self.board = KanbanBoard(columns=[self.src, self.wip, self.done])

    def test_move_card_appends_to_target_by_default(self):
        self.board.move_card(self.card.id, self.wip.id)
        assert len(self.src.cards) == 0
        assert len(self.wip.cards) == 1
        assert self.wip.cards[0].id == self.card.id

    def test_move_card_inserts_at_position(self):
        existing = KanbanCard(name="Existing")
        self.wip.cards.append(existing)
        self.board.move_card(self.card.id, self.wip.id, position=0)
        assert self.wip.cards[0].id == self.card.id
        assert self.wip.cards[1].id == existing.id

    def test_move_card_clamps_position_to_valid_range(self):
        self.board.move_card(self.card.id, self.wip.id, position=999)
        assert self.wip.cards[-1].id == self.card.id

    def test_move_card_raises_for_unknown_card(self):
        with pytest.raises(ValueError, match="not found"):
            self.board.move_card("bad-id", self.wip.id)

    def test_move_card_raises_for_unknown_column(self):
        with pytest.raises(ValueError, match="not found"):
            self.board.move_card(self.card.id, "bad-col-id")

    def test_move_card_to_same_column_no_duplicate_or_loss(self):
        # Moving a card to its own column appends it to the end — no duplicate,
        # no card lost, total count stays the same
        extra = KanbanCard(name="Extra")
        self.src.cards.append(extra)
        self.board.move_card(self.card.id, self.src.id)
        assert len(self.src.cards) == 2
        ids = [c.id for c in self.src.cards]
        assert self.card.id in ids
        assert extra.id in ids


# ---------------------------------------------------------------------------
# KanbanBoard — reorder_cards
# ---------------------------------------------------------------------------

class TestKanbanBoardReorderCards:
    def setup_method(self):
        self.c1 = KanbanCard(name="C1")
        self.c2 = KanbanCard(name="C2")
        self.c3 = KanbanCard(name="C3")
        self.wip = KanbanColumn(name="WIP", is_wip=True, cards=[self.c1, self.c2, self.c3])
        self.board = KanbanBoard(columns=[self.wip])

    def test_reorder_cards_happy_path(self):
        self.board.reorder_cards(self.wip.id, [self.c3.id, self.c1.id, self.c2.id])
        assert [c.id for c in self.wip.cards] == [self.c3.id, self.c1.id, self.c2.id]

    def test_reorder_cards_raises_for_missing_column(self):
        with pytest.raises(ValueError, match="not found"):
            self.board.reorder_cards("bad-col", [self.c1.id])

    def test_reorder_cards_raises_for_wrong_ids(self):
        # Providing an id that doesn't belong to the column should raise
        with pytest.raises(ValueError, match="exactly the same ids"):
            self.board.reorder_cards(self.wip.id, [self.c1.id, "stranger"])

    def test_reorder_cards_raises_for_missing_ids(self):
        # Omitting one existing id should also raise
        with pytest.raises(ValueError, match="exactly the same ids"):
            self.board.reorder_cards(self.wip.id, [self.c1.id, self.c2.id])


# ---------------------------------------------------------------------------
# KanbanBoard — add_column / rename_column / remove_column
# ---------------------------------------------------------------------------

class TestKanbanBoardColumnManagement:
    def setup_method(self):
        self.wip = KanbanColumn(name="In Progress", is_wip=True)
        self.done = KanbanColumn(name="Done")
        self.board = KanbanBoard(columns=[self.wip, self.done])

    def test_add_column_appends_by_default(self):
        col = self.board.add_column("Archive")
        assert self.board.columns[-1].id == col.id
        assert col.name == "Archive"

    def test_add_column_inserts_at_position(self):
        col = self.board.add_column("Review", position=0)
        assert self.board.columns[0].id == col.id

    def test_add_column_returns_new_column(self):
        col = self.board.add_column("Extra", is_wip=True)
        assert col.is_wip is True
        assert col in self.board.columns

    def test_rename_column_changes_name(self):
        self.board.rename_column(self.done.id, "Completed")
        assert self.done.name == "Completed"

    def test_rename_column_raises_for_unknown_id(self):
        with pytest.raises(ValueError, match="not found"):
            self.board.rename_column("bad-id", "X")

    def test_remove_column_removes_empty_column(self):
        self.board.remove_column(self.done.id)
        assert self.done not in self.board.columns

    def test_remove_column_raises_for_unknown_id(self):
        with pytest.raises(ValueError, match="not found"):
            self.board.remove_column("bad-id")

    def test_remove_last_wip_column_raises_even_with_force(self):
        # Can never remove the last WIP column — the board invariant must hold
        with pytest.raises(ValueError, match="last WIP"):
            self.board.remove_column(self.wip.id, force=True)

    def test_remove_column_with_cards_raises_without_force(self):
        card = KanbanCard(name="Stuck card")
        self.done.cards.append(card)
        with pytest.raises(ValueError, match="still has"):
            self.board.remove_column(self.done.id)

    def test_remove_column_with_cards_succeeds_with_force(self):
        card = KanbanCard(name="Stuck card")
        self.done.cards.append(card)
        self.board.remove_column(self.done.id, force=True)
        assert self.done not in self.board.columns

    def test_remove_wip_column_when_another_wip_exists(self):
        extra_wip = self.board.add_column("WIP-2", is_wip=True)
        # Should succeed because self.wip still remains
        self.board.remove_column(extra_wip.id)
        assert extra_wip not in self.board.columns


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class TestProject:
    def test_default_board_has_four_columns(self):
        p = Project(name="My project")
        assert len(p.board.columns) == 4

    def test_default_board_column_names(self):
        p = Project(name="My project")
        names = [c.name for c in p.board.columns]
        assert names == ["Todo", "In Progress", "Pending", "Done"]

    def test_default_board_has_exactly_one_wip_column(self):
        p = Project(name="My project")
        wip_cols = [c for c in p.board.columns if c.is_wip]
        assert len(wip_cols) == 1
        assert wip_cols[0].name == "In Progress"

    def test_archived_defaults_to_false(self):
        p = Project(name="New")
        assert p.archived is False

    def test_target_weekly_hours_defaults_to_none(self):
        p = Project(name="New")
        assert p.target_weekly_hours is None

    def test_two_projects_have_independent_boards(self):
        # Each project must get its own board with fresh column ids,
        # not a shared mutable default object
        p1 = Project(name="P1")
        p2 = Project(name="P2")
        assert p1.board is not p2.board
        assert p1.board.columns[0].id != p2.board.columns[0].id


# ---------------------------------------------------------------------------
# TimeBlock / WeeklyPlan
# ---------------------------------------------------------------------------

class TestTimeBlock:
    def test_valid_block(self):
        block = TimeBlock(
            project_id="proj-1",
            day="monday",
            start_time="09:00",
            end_time="11:00",
        )
        assert block.day == "monday"
        assert block.notes == ""

    def test_invalid_day_raises(self):
        with pytest.raises(ValidationError):
            TimeBlock(project_id="p", day="funday", start_time="09:00", end_time="10:00")

    def test_all_valid_days_accepted(self):
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        for day in days:
            block = TimeBlock(project_id="p", day=day, start_time="08:00", end_time="09:00")
            assert block.day == day


class TestWeeklyPlan:
    def test_defaults(self):
        plan = WeeklyPlan(week="2026-W10")
        assert plan.week == "2026-W10"
        assert plan.time_blocks == []
        assert plan.constraints == ""
        assert plan.session_notes == ""

    def test_timestamps_are_timezone_aware(self):
        plan = WeeklyPlan(week="2026-W10")
        assert plan.created_at.tzinfo is not None
        assert plan.updated_at.tzinfo is not None

    def test_accepts_time_blocks(self):
        block = TimeBlock(project_id="p", day="friday", start_time="14:00", end_time="16:00")
        plan = WeeklyPlan(week="2026-W10", time_blocks=[block])
        assert len(plan.time_blocks) == 1


# ---------------------------------------------------------------------------
# InboxNote
# ---------------------------------------------------------------------------

class TestInboxNote:
    def test_defaults(self):
        note = InboxNote(content="Buy milk")
        assert note.content == "Buy milk"
        assert note.addressed is False
        assert note.addressed_at is None

    def test_addressed_fields_can_be_set(self):
        ts = datetime.now(timezone.utc)
        note = InboxNote(content="Done", addressed=True, addressed_at=ts)
        assert note.addressed is True
        assert note.addressed_at == ts

    def test_id_is_unique(self):
        a = InboxNote(content="A")
        b = InboxNote(content="B")
        assert a.id != b.id


# ---------------------------------------------------------------------------
# BehavioralProfile / ProfileUpdate
# ---------------------------------------------------------------------------

class TestBehavioralProfile:
    def test_defaults(self):
        profile = BehavioralProfile()
        assert profile.content == ""
        assert profile.history == []
        assert profile.last_updated is None

    def test_history_accepts_profile_updates(self):
        update = ProfileUpdate(summary="Added morning preference")
        profile = BehavioralProfile(history=[update])
        assert len(profile.history) == 1
        assert profile.history[0].summary == "Added morning preference"


class TestProfileUpdate:
    def test_date_defaults_to_utc_now(self):
        before = datetime.now(timezone.utc)
        update = ProfileUpdate(summary="First entry")
        after = datetime.now(timezone.utc)
        assert before <= update.date <= after

    def test_date_is_timezone_aware(self):
        update = ProfileUpdate(summary="X")
        assert update.date.tzinfo is not None
