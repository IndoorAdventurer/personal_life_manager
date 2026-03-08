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
