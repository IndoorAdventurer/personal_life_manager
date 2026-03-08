from uuid import uuid4

from pydantic import BaseModel, Field

from plm.models.card import KanbanCard


class KanbanColumn(BaseModel):
    # uuid4 string — stable identifier even if the column is renamed
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: str = ""
    # Marks this column as the "in-progress" lane; the board validator
    # enforces that exactly one (or at least one) WIP column exists
    is_wip: bool = False
    cards: list[KanbanCard] = Field(default_factory=list)
