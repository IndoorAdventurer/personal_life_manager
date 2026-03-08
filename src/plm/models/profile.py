from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ProfileUpdate(BaseModel):
    """A single timestamped entry in the profile's change history."""
    date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Human-readable summary of what changed and why (written by Claude)
    summary: str


class BehavioralProfile(BaseModel):
    # Free-form markdown text — Claude reads this before creating a schedule.
    # Intentionally unstructured: richer natural language context is more
    # useful to an LLM than a handful of typed fields, and easier to update
    # incrementally without a schema migration.
    # Example content: "I do my best deep work in the morning. Rarely more
    # than 4 focused hours a day. I get distracted after lunch. No meetings
    # on Fridays if possible."
    content: str = ""
    # Append-only audit trail — each weekly review appends an entry summarising
    # what changed and why, so the evolution of the profile is traceable
    history: list[ProfileUpdate] = Field(default_factory=list)
    last_updated: datetime | None = None
