from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CardLog(BaseModel):
    # Timestamp set automatically on creation — callers only provide the message
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str


def normalize_tags(tags: list[str]) -> list[str]:
    """Trim whitespace, drop empties, and dedupe case-insensitively.

    The first spelling wins, so ["v0.1", "V0.1 "] becomes ["v0.1"]. Order is
    kept as entered. Lives here (not in the web/MCP layers) so every way of
    setting tags behaves the same.
    """
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        tag = tag.strip()
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            result.append(tag)
    return result


class KanbanCard(BaseModel):
    # Validate on assignment too, so `card.tags = [...]` in the web and MCP
    # layers goes through normalize_tags just like construction does
    model_config = ConfigDict(validate_assignment=True)

    # uuid4 string — generated at creation, never changed
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: str = ""
    # Append-only progress notes; we never edit or remove individual entries
    logs: list[CardLog] = Field(default_factory=list)
    # Free-text workload estimate (e.g. "2h", "small") — optional by design
    # so cards can exist before the user has sized them
    estimated_workload: str | None = None
    # Free-text labels shown as coloured badges (e.g. "v0.1"). The colour is
    # derived from the name, so there's no tag registry to manage. Defaults to
    # empty so cards saved before tags existed still load.
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, tags: list[str]) -> list[str]:
        return normalize_tags(tags)
