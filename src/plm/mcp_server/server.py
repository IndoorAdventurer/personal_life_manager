"""
PLM MCP server — all tool definitions.

Uses FastMCP (mcp.server.fastmcp.FastMCP), which turns decorated Python
functions into MCP tools automatically.  Each function:
  - takes plain typed arguments (FastMCP generates the JSON schema from them)
  - returns a plain dict (FastMCP serialises it for the protocol)
  - raises ValueError for user-facing errors (invalid ids, constraint violations)

The JsonStore is instantiated once at module load time so all tools share the
same data-directory resolution (PLM_DATA_DIR env var or ~/.local/share/plm/).
Passing the store as a module-level singleton also makes it easy to swap in a
test store by patching `plm.mcp_server.server.store` in tests.
"""

import warnings
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from plm.models.card import CardLog, KanbanCard
from plm.models.column import KanbanColumn
from plm.models.inbox import InboxNote
from plm.models.planning import TimeBlock, WeeklyPlan
from plm.models.profile import BehavioralProfile, ProfileUpdate
from plm.models.project import Project
from plm.storage.store import JsonStore

mcp = FastMCP("Personal Life Manager")

# Module-level store — one instance shared by all tools.  Tests override this
# by doing:  import plm.mcp_server.server as srv; srv.store = JsonStore(tmp_dir)
store = JsonStore()

# Day order used when sorting time blocks for display
_DAY_ORDER = {
    day: i for i, day in enumerate(
        ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    )
}
_VALID_DAYS = set(_DAY_ORDER)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_project(project_id: str) -> Project:
    """
    Load a project or raise ValueError.

    Centralised so all tools produce the same error message and None-checks
    aren't repeated everywhere.
    """
    project = store.get_project(project_id)
    if project is None:
        raise ValueError(f"Project {project_id!r} not found")
    return project


def _require_card(
    project_id: str, card_id: str
) -> tuple[Project, KanbanColumn, KanbanCard]:
    """
    Load a project and locate a card within it, or raise ValueError.

    Returns (project, column, card) so callers can mutate the card in place
    and then call store.save_project(project).
    """
    project = _require_project(project_id)
    result = project.board.find_card(card_id)
    if result is None:
        raise ValueError(f"Card {card_id!r} not found in project {project_id!r}")
    col, card = result
    return project, col, card


def _current_week() -> str:
    """Return the current ISO week string, e.g. '2026-W10'."""
    now = datetime.now(timezone.utc)
    # isocalendar() returns (year, week, weekday); zero-pad week to 2 digits
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def _parse_hhmm(value: str, field: str) -> None:
    """
    Validate that *value* is a valid "HH:MM" time string.

    Raises ValueError with a clear message if not.  Called by tools that
    accept start_time / end_time to catch bad input before it lands on disk.
    """
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"{field} must be 'HH:MM', got {value!r}")
    h, m = parts
    if not (h.isdigit() and m.isdigit() and 0 <= int(h) <= 23 and 0 <= int(m) <= 59):
        raise ValueError(f"{field} must be 'HH:MM' with valid hours/minutes, got {value!r}")


def _hhmm_to_minutes(value: str) -> int:
    """Convert a validated 'HH:MM' string to total minutes since midnight."""
    h, m = value.split(":")
    return int(h) * 60 + int(m)


def _sort_key_for_block(block: TimeBlock) -> tuple[int, int]:
    """Sort key for time blocks: day order first, then start_time in minutes."""
    return (_DAY_ORDER.get(block.day, 99), _hhmm_to_minutes(block.start_time))


def _serialise_blocks(blocks: list[TimeBlock]) -> list[dict]:
    """Return time blocks sorted by day + start_time, ready for JSON output."""
    sorted_blocks = sorted(blocks, key=_sort_key_for_block)
    return [b.model_dump(mode="json") for b in sorted_blocks]


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@mcp.tool()
def list_projects(include_archived: bool = False) -> dict:
    """
    Return a lightweight list of all projects (id, name, description,
    target_weekly_hours, archived).

    Intentionally minimal — call get_project() or list_columns() when you
    need board details.  Archived projects are excluded unless
    include_archived=True.  Corrupt project files are reported in 'warnings'.
    """
    corrupt_warnings = []

    # Capture warnings emitted by store.list_projects() for corrupt files
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        all_projects = store.list_projects()

    for w in caught:
        corrupt_warnings.append(str(w.message))

    projects = []
    for p in all_projects:
        if not include_archived and p.archived:
            continue
        projects.append({
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "target_weekly_hours": p.target_weekly_hours,
            "archived": p.archived,
        })

    result: dict = {"projects": projects}
    if corrupt_warnings:
        result["warnings"] = corrupt_warnings
    return result


@mcp.tool()
def create_project(
    name: str,
    description: str = "",
    target_weekly_hours: float | None = None,
) -> dict:
    """Create a new project with the default Todo/In Progress/Pending/Done board."""
    project = Project(
        name=name,
        description=description,
        target_weekly_hours=target_weekly_hours,
    )
    store.save_project(project)
    return {"ok": True, "project_id": project.id, "name": project.name}


@mcp.tool()
def get_project(project_id: str) -> dict:
    """
    Return the full project including all columns and cards.

    Use this when you need complete card details.  For lightweight lookups,
    prefer list_columns() or list_cards().
    """
    project = _require_project(project_id)
    return project.model_dump(mode="json")


@mcp.tool()
def update_project(
    project_id: str,
    name: str | None = None,
    description: str | None = None,
    target_weekly_hours: float | None = None,
) -> dict:
    """
    Update project metadata. Only the provided fields are changed.

    Omitting a field (or passing None) leaves it unchanged — this tool cannot
    clear a field back to None/empty.
    """
    project = _require_project(project_id)
    if name is not None:
        project.name = name
    if description is not None:
        project.description = description
    if target_weekly_hours is not None:
        project.target_weekly_hours = target_weekly_hours
    store.save_project(project)
    return {"ok": True, "project_id": project.id}


@mcp.tool()
def archive_project(project_id: str) -> dict:
    """
    Mark a project as archived.

    Archived projects are hidden from list_projects() by default and from the
    web UI, but their files remain on disk so planning history stays intact.
    """
    project = _require_project(project_id)
    project.archived = True
    store.save_project(project)
    return {"ok": True, "project_id": project.id}


@mcp.tool()
def get_wip_overview() -> dict:
    """
    Return a compact summary of all cards currently in a WIP column across
    all active projects.

    Returns project name and, per card, its name and id.  The id lets you act
    on a card (move, log, update) without a separate get_project() call.
    Use get_project() if you need full card details.
    """
    overview = []
    for project in store.list_projects():
        if project.archived:
            continue
        wip_cards = project.board.get_wip_cards()
        if wip_cards:
            overview.append({
                "project": project.name,
                "project_id": project.id,
                "wip_cards": [{"id": card.id, "name": card.name} for card in wip_cards],
            })
    return {"wip": overview}


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

@mcp.tool()
def list_columns(project_id: str) -> dict:
    """
    Return all columns for a project with their id, name, is_wip flag, and
    card count.

    Lighter than get_project() when you only need the board structure without
    card details.
    """
    project = _require_project(project_id)
    return {
        "project_id": project_id,
        "columns": [
            {
                "id": col.id,
                "name": col.name,
                "is_wip": col.is_wip,
                "card_count": len(col.cards),
            }
            for col in project.board.columns
        ],
    }


@mcp.tool()
def add_column(
    project_id: str,
    name: str,
    is_wip: bool = False,
    position: int | None = None,
) -> dict:
    """Add a new empty column to a project's board."""
    project = _require_project(project_id)
    col = project.board.add_column(name=name, is_wip=is_wip, position=position)
    store.save_project(project)
    return {"ok": True, "column_id": col.id, "name": col.name}


@mcp.tool()
def rename_column(project_id: str, column_id: str, name: str) -> dict:
    """Rename a column on a project's board."""
    project = _require_project(project_id)
    # board.rename_column raises ValueError if column_id not found
    project.board.rename_column(column_id=column_id, name=name)
    store.save_project(project)
    return {"ok": True, "column_id": column_id, "name": name}


@mcp.tool()
def remove_column(project_id: str, column_id: str, force: bool = False) -> dict:
    """
    Remove a column from a project's board.

    force=False (default): refuses if the column still has cards.
    force=True: deletes the column and all its cards permanently.
    Always refuses if it is the last WIP column.
    """
    project = _require_project(project_id)
    # board.remove_column raises ValueError for last-WIP and non-empty-without-force
    project.board.remove_column(column_id=column_id, force=force)
    store.save_project(project)
    return {"ok": True, "column_id": column_id}


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

@mcp.tool()
def list_cards(project_id: str, column_id: str) -> dict:
    """
    Return all cards in a specific column (id, name, estimated_workload).

    Lighter than get_project() when you only need one column's cards.
    If you don't have the column_id yet, call list_columns(project_id) first.
    """
    project = _require_project(project_id)
    col = next((c for c in project.board.columns if c.id == column_id), None)
    if col is None:
        raise ValueError(f"Column {column_id!r} not found in project {project_id!r}")
    return {
        "column_id": column_id,
        "column_name": col.name,
        "cards": [
            {"id": card.id, "name": card.name, "estimated_workload": card.estimated_workload}
            for card in col.cards
        ],
    }


@mcp.tool()
def get_card(project_id: str, card_id: str) -> dict:
    """
    Return the full details of a single card, including description and logs.

    Use this when you need to read a card's description or log history.
    list_cards() is lighter if you only need names and ids.
    """
    _project, _col, card = _require_card(project_id, card_id)
    return card.model_dump(mode="json")


@mcp.tool()
def add_card(
    project_id: str,
    column_id: str,
    name: str,
    description: str = "",
    estimated_workload: str | None = None,
) -> dict:
    """
    Add a new card to a specific column.

    Both project_id and column_id are required — column_id alone is not
    sufficient to identify the destination.  If you don't have the column_id
    yet, call list_columns(project_id) first to get it.
    """
    project = _require_project(project_id)
    col = next((c for c in project.board.columns if c.id == column_id), None)
    if col is None:
        raise ValueError(f"Column {column_id!r} not found in project {project_id!r}")

    card = KanbanCard(
        name=name,
        description=description,
        estimated_workload=estimated_workload,
    )
    col.cards.append(card)
    store.save_project(project)
    return {"ok": True, "card_id": card.id, "name": card.name, "column_id": column_id}


@mcp.tool()
def move_card(
    project_id: str,
    card_id: str,
    target_column_id: str,
    position: int | None = None,
) -> dict:
    """
    Move a card to a different column (or reposition within the same column).

    position=None appends to the end of the target column.
    """
    project, _col, card = _require_card(project_id, card_id)
    # board.move_card raises ValueError if target column not found
    project.board.move_card(
        card_id=card_id,
        target_column_id=target_column_id,
        position=position,
    )
    # Stamp updated_at — board.move_card relocates the card but doesn't touch
    # timestamps (that's the MCP layer's responsibility per our design notes)
    card.updated_at = datetime.now(timezone.utc)
    store.save_project(project)
    return {"ok": True, "card_id": card_id, "target_column_id": target_column_id}


@mcp.tool()
def reorder_cards(project_id: str, column_id: str, card_ids: list[str]) -> dict:
    """
    Reorder cards within a column.

    card_ids must contain exactly the same ids currently in the column —
    raises an error if any are missing or extra are included.
    """
    project = _require_project(project_id)
    # board.reorder_cards raises ValueError if ids don't match
    project.board.reorder_cards(column_id=column_id, card_ids=card_ids)
    store.save_project(project)
    return {"ok": True, "column_id": column_id}


@mcp.tool()
def update_card(
    project_id: str,
    card_id: str,
    name: str | None = None,
    description: str | None = None,
    estimated_workload: str | None = None,
) -> dict:
    """
    Update card fields. Only the provided fields are changed.

    Omitting a field (or passing None) leaves it unchanged — this tool cannot
    clear a field back to None/empty; use an explicit empty string for that.
    """
    project, _col, card = _require_card(project_id, card_id)
    if name is not None:
        card.name = name
    if description is not None:
        card.description = description
    if estimated_workload is not None:
        card.estimated_workload = estimated_workload
    card.updated_at = datetime.now(timezone.utc)
    store.save_project(project)
    return {"ok": True, "card_id": card_id}


@mcp.tool()
def append_card_log(project_id: str, card_id: str, log_entry: str) -> dict:
    """Append a progress note to a card's log. Logs are append-only."""
    project, _col, card = _require_card(project_id, card_id)
    card.logs.append(CardLog(message=log_entry))
    card.updated_at = datetime.now(timezone.utc)
    store.save_project(project)
    return {"ok": True, "card_id": card_id, "log_count": len(card.logs)}


@mcp.tool()
def delete_card(project_id: str, card_id: str) -> dict:
    """Permanently delete a card from a project."""
    project, col, card = _require_card(project_id, card_id)
    col.cards.remove(card)
    store.save_project(project)
    return {"ok": True, "card_id": card_id}


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

@mcp.tool()
def get_plan(week: str | None = None) -> dict:
    """
    Return the weekly plan for a given week, with time blocks sorted by
    day (monday → sunday) then start_time.

    week defaults to the current ISO week (e.g. '2026-W10').
    Returns an empty plan structure if no plan exists for that week yet.
    """
    if week is None:
        week = _current_week()
    plan = store.get_plan(week)
    if plan is None:
        # Return an empty structure rather than null so the caller doesn't need
        # to special-case a missing plan — the week just has no blocks yet
        return {"week": week, "time_blocks": [], "constraints": "", "session_notes": ""}

    data = plan.model_dump(mode="json")
    # Replace the raw (insertion-order) blocks with a sorted version
    data["time_blocks"] = _serialise_blocks(plan.time_blocks)
    return data


@mcp.tool()
def create_plan(week: str, constraints: str = "", session_notes: str = "") -> dict:
    """
    Create (or reset) the weekly plan for a given week.

    If a plan already exists for the week it is overwritten.  Use this to
    initialise a fresh plan at the start of a weekly review session.
    """
    plan = WeeklyPlan(week=week, constraints=constraints, session_notes=session_notes)
    store.save_plan(plan)
    return {"ok": True, "week": week}


@mcp.tool()
def add_time_block(
    week: str,
    project_id: str,
    day: str,
    start_time: str,
    end_time: str,
    notes: str = "",
) -> dict:
    """
    Add a time block to a weekly plan.

    Creates the plan for the week if it doesn't exist yet.
    week must be an ISO week string, e.g. '2026-W10'.
    day must be one of: monday tuesday wednesday thursday friday saturday sunday.
    start_time / end_time must be 'HH:MM', and end_time must be after start_time
    (blocks spanning midnight are not supported — split into two blocks instead).
    """
    _parse_hhmm(start_time, "start_time")
    _parse_hhmm(end_time, "end_time")

    if _hhmm_to_minutes(end_time) <= _hhmm_to_minutes(start_time):
        raise ValueError(
            f"end_time ({end_time}) must be after start_time ({start_time}). "
            "Blocks spanning midnight are not supported — split into two blocks."
        )

    if day not in _VALID_DAYS:
        raise ValueError(f"day must be one of {sorted(_VALID_DAYS)}, got {day!r}")

    # Verify the project exists so we don't store orphaned blocks
    _require_project(project_id)

    plan = store.get_plan(week) or WeeklyPlan(week=week)
    block = TimeBlock(
        project_id=project_id,
        day=day,  # type: ignore[arg-type]  # validated above
        start_time=start_time,
        end_time=end_time,
        notes=notes,
    )
    plan.time_blocks.append(block)
    plan.updated_at = datetime.now(timezone.utc)
    store.save_plan(plan)
    return {"ok": True, "block_id": block.id, "week": week, "day": day}


@mcp.tool()
def remove_time_block(week: str, block_id: str) -> dict:
    """
    Remove a time block from a weekly plan.

    week must be an ISO week string, e.g. '2026-W10'.
    block_id is returned by add_time_block() and is also present in get_plan().
    """
    plan = store.get_plan(week)
    if plan is None:
        raise ValueError(f"No plan found for week {week!r}")

    block = next((b for b in plan.time_blocks if b.id == block_id), None)
    if block is None:
        raise ValueError(f"Time block {block_id!r} not found in week {week!r}")

    plan.time_blocks.remove(block)
    plan.updated_at = datetime.now(timezone.utc)
    store.save_plan(plan)
    return {"ok": True, "block_id": block_id}


@mcp.tool()
def update_time_block(
    week: str,
    block_id: str,
    day: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    notes: str | None = None,
) -> dict:
    """
    Update fields on an existing time block. Only provided fields are changed.

    week must be an ISO week string, e.g. '2026-W10'.
    block_id is returned by add_time_block() and is also present in get_plan().
    After applying changes, end_time must still be after start_time.
    """
    plan = store.get_plan(week)
    if plan is None:
        raise ValueError(f"No plan found for week {week!r}")

    block = next((b for b in plan.time_blocks if b.id == block_id), None)
    if block is None:
        raise ValueError(f"Time block {block_id!r} not found in week {week!r}")

    if day is not None:
        if day not in _VALID_DAYS:
            raise ValueError(f"day must be one of {sorted(_VALID_DAYS)}, got {day!r}")
        block.day = day  # type: ignore[assignment]  # validated above
    if start_time is not None:
        _parse_hhmm(start_time, "start_time")
        block.start_time = start_time
    if end_time is not None:
        _parse_hhmm(end_time, "end_time")
        block.end_time = end_time
    if notes is not None:
        block.notes = notes

    # Validate end > start after all updates are applied
    if _hhmm_to_minutes(block.end_time) <= _hhmm_to_minutes(block.start_time):
        raise ValueError(
            f"end_time ({block.end_time}) must be after start_time ({block.start_time})"
        )

    plan.updated_at = datetime.now(timezone.utc)
    store.save_plan(plan)
    return {"ok": True, "block_id": block_id}


@mcp.tool()
def get_weekly_hours_summary(week: str | None = None) -> dict:
    """
    Return total planned hours per project for a given week.

    week defaults to the current ISO week.
    Hours are calculated from time block durations (end_time - start_time).
    """
    if week is None:
        week = _current_week()

    plan = store.get_plan(week)
    if plan is None:
        return {"week": week, "total_hours": 0.0, "by_project": []}

    # Accumulate minutes per project_id
    minutes_by_project: dict[str, float] = {}
    for block in plan.time_blocks:
        duration = _hhmm_to_minutes(block.end_time) - _hhmm_to_minutes(block.start_time)
        if duration > 0:
            minutes_by_project[block.project_id] = (
                minutes_by_project.get(block.project_id, 0) + duration
            )

    # Resolve project names for a readable response
    by_project = []
    for pid, minutes in minutes_by_project.items():
        project = store.get_project(pid)
        name = project.name if project else f"<deleted project {pid}>"
        by_project.append({
            "project_id": pid,
            "project_name": name,
            "planned_hours": round(minutes / 60, 2),
        })

    by_project.sort(key=lambda x: x["planned_hours"], reverse=True)
    total = round(sum(x["planned_hours"] for x in by_project), 2)
    return {"week": week, "total_hours": total, "by_project": by_project}


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

@mcp.tool()
def add_inbox_note(content: str) -> dict:
    """Add a new note to the inbox (quick-capture)."""
    note = InboxNote(content=content)
    store.add_inbox_note(note)
    return {"ok": True, "note_id": note.id}


@mcp.tool()
def list_inbox_notes(include_addressed: bool = False) -> dict:
    """
    Return inbox notes.

    By default only unaddressed notes are returned.
    Set include_addressed=True to see the full history.
    """
    notes = store.get_inbox()
    if not include_addressed:
        notes = [n for n in notes if not n.addressed]
    return {
        "notes": [
            {
                "id": n.id,
                "content": n.content,
                "created_at": n.created_at.isoformat(),
                "addressed": n.addressed,
            }
            for n in notes
        ]
    }


@mcp.tool()
def mark_inbox_note_addressed(note_id: str) -> dict:
    """Mark an inbox note as addressed (soft delete — keeps it in history)."""
    notes = store.get_inbox()
    note = next((n for n in notes if n.id == note_id), None)
    if note is None:
        raise ValueError(f"Inbox note {note_id!r} not found")
    note.addressed = True
    note.addressed_at = datetime.now(timezone.utc)
    store.save_inbox(notes)
    return {"ok": True, "note_id": note_id}


@mcp.tool()
def delete_inbox_note(note_id: str) -> dict:
    """
    Permanently delete an inbox note.

    Use mark_inbox_note_addressed() for the normal workflow (keeps history).
    This is for notes captured by mistake or containing sensitive content.
    """
    notes = store.get_inbox()
    note = next((n for n in notes if n.id == note_id), None)
    if note is None:
        raise ValueError(f"Inbox note {note_id!r} not found")
    notes.remove(note)
    store.save_inbox(notes)
    return {"ok": True, "note_id": note_id}


# ---------------------------------------------------------------------------
# Behavioral profile
# ---------------------------------------------------------------------------

@mcp.tool()
def get_behavioral_profile() -> dict:
    """
    Return the behavioral profile content and last_updated timestamp.

    The change history is omitted to keep context lean — use
    get_profile_history() if you need the audit trail.
    """
    profile = store.get_profile()
    return {
        "content": profile.content,
        "last_updated": profile.last_updated.isoformat() if profile.last_updated else None,
    }


@mcp.tool()
def get_profile_history() -> dict:
    """
    Return the full audit trail of behavioral profile updates.

    Each entry has a date and a human-readable summary of what changed.
    Kept separate from get_behavioral_profile() so the history doesn't
    clutter context during normal planning sessions.
    """
    profile = store.get_profile()
    return {
        "history": [
            {"date": entry.date.isoformat(), "summary": entry.summary}
            for entry in profile.history
        ]
    }


@mcp.tool()
def update_behavioral_profile(content: str, summary: str) -> dict:
    """
    Replace the full profile content and append a history entry.

    content: the new full markdown text for the profile.
    summary: a short description of what changed and why (appended to the
             audit trail so the profile's evolution is traceable).
    """
    profile = store.get_profile()
    profile.content = content
    profile.history.append(ProfileUpdate(summary=summary))
    profile.last_updated = datetime.now(timezone.utc)
    store.save_profile(profile)
    return {"ok": True, "history_entries": len(profile.history)}


@mcp.tool()
def patch_behavioral_profile(old_text: str, new_text: str, summary: str) -> dict:
    """
    Make a targeted edit to the behavioral profile — analogous to a file
    patch rather than a full rewrite.

    old_text: the exact substring to find in the current profile content.
              Must appear exactly once; raises ValueError otherwise.
    new_text: the replacement text (can be empty to delete old_text).
    summary:  short description of what changed and why (appended to the
              audit trail, same as update_behavioral_profile).

    Prefer this over update_behavioral_profile when only one section needs
    to change — it is more token-efficient and avoids accidentally dropping
    other parts of the profile.
    """
    profile = store.get_profile()

    count = profile.content.count(old_text)
    if count == 0:
        raise ValueError("old_text not found in profile content")
    if count > 1:
        raise ValueError(
            f"old_text matches {count} times — make it more specific so the "
            "patch target is unambiguous"
        )

    profile.content = profile.content.replace(old_text, new_text, 1)
    profile.history.append(ProfileUpdate(summary=summary))
    profile.last_updated = datetime.now(timezone.utc)
    store.save_profile(profile)
    return {"ok": True, "history_entries": len(profile.history)}


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@mcp.tool()
def get_weekly_review_data(week: str | None = None) -> dict:
    """
    Return all data needed for a weekly review session in one call.

    Includes: the week's plan (blocks sorted by day + time), current WIP cards
    (project name + card name + card id), unaddressed inbox notes, and the
    behavioral profile content.

    week defaults to the current ISO week.  Pass the previous week's string
    (e.g. '2026-W09') to review a past week.
    """
    if week is None:
        week = _current_week()

    plan = store.get_plan(week)
    if plan is None:
        plan_data: dict = {"week": week, "time_blocks": [], "constraints": "", "session_notes": ""}
    else:
        plan_data = plan.model_dump(mode="json")
        plan_data["time_blocks"] = _serialise_blocks(plan.time_blocks)

    # WIP: project name + card name + card id (enough to act without a follow-up call)
    wip = []
    for project in store.list_projects():
        if project.archived:
            continue
        cards = project.board.get_wip_cards()
        if cards:
            wip.append({
                "project": project.name,
                "project_id": project.id,
                "wip_cards": [{"id": card.id, "name": card.name} for card in cards],
            })

    inbox_notes = [
        {"id": n.id, "content": n.content, "created_at": n.created_at.isoformat()}
        for n in store.get_inbox()
        if not n.addressed
    ]

    profile = store.get_profile()

    return {
        "week": week,
        "plan": plan_data,
        "wip": wip,
        "inbox": inbox_notes,
        "profile": {"content": profile.content},
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server over stdio (invoked via the plm-mcp entry point)."""
    mcp.run()
