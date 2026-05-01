from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

# Valid day names — Literal gives us free validation + IDE autocomplete
Day = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class TimeBlock(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    day: Day
    # Times stored as "HH:MM" strings rather than time objects because:
    # 1. JSON serialisation is trivial (no custom encoder needed)
    # 2. The UI and MCP tools deal in human-readable strings anyway
    start_time: str  # "HH:MM"
    end_time: str    # "HH:MM"
    notes: str = ""


# Input model for add_time_blocks — separates the user-supplied fields from the
# server-assigned ones (id, generated at save time).  Using a typed model rather
# than a raw dict lets FastMCP produce a proper JSON schema, which gives LLMs
# accurate structured guidance when calling the tool.
class TimeBlockInput(BaseModel):
    project_id: str
    # day, start_time, end_time are plain strings here so that Pydantic does not
    # validate them at construction time — the MCP tool validates them explicitly
    # with index-tagged error messages (e.g. "blocks[2]: day must be one of …").
    # The docstring on add_time_blocks() documents the accepted values for LLMs.
    day: str        # monday | tuesday | wednesday | thursday | friday | saturday | sunday
    start_time: str  # "HH:MM"
    end_time: str    # "HH:MM"
    notes: str = ""


class WeeklyPlan(BaseModel):
    # ISO week string, e.g. "2026-W10" — used as the filename key in storage
    week: str
    time_blocks: list[TimeBlock] = Field(default_factory=list)
    # One-off constraints for this specific week — read by Claude before
    # generating the schedule. Not part of the profile because they don't
    # reflect long-term patterns (e.g. "Wednesday evening blocked, have dinner plans")
    constraints: str = ""
    # Freeform notes captured during the weekly planning session
    session_notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
