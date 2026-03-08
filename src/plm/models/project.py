from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from plm.models.board import KanbanBoard
from plm.models.column import KanbanColumn


def _default_board() -> KanbanBoard:
    """
    Create the standard three-column board that every new project starts with.

    Todo → In Progress (WIP) → Done
    Using a factory function (not a class-level default) so that each Project
    gets its own independent board instance with fresh uuid4 column ids.
    """
    return KanbanBoard(
        columns=[
            KanbanColumn(name="Todo"),
            KanbanColumn(name="In Progress", is_wip=True),
            KanbanColumn(name="Pending"),
            KanbanColumn(name="Done"),
        ]
    )


class Project(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: str = ""
    # Optional weekly hour target — used by the planning feature but not required
    target_weekly_hours: float | None = None
    board: KanbanBoard = Field(default_factory=_default_board)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Archived projects are hidden from the default list but never deleted,
    # so historical planning data referencing them remains valid
    archived: bool = False
