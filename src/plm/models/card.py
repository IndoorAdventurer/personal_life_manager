from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


class CardLog(BaseModel):
    # Timestamp set automatically on creation — callers only provide the message
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str


class KanbanCard(BaseModel):
    # uuid4 string — generated at creation, never changed
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: str = ""
    # Append-only progress notes; we never edit or remove individual entries
    logs: list[CardLog] = Field(default_factory=list)
    # Free-text workload estimate (e.g. "2h", "small") — optional by design
    # so cards can exist before the user has sized them
    estimated_workload: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
