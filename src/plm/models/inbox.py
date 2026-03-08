from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


class InboxNote(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    addressed: bool = False
    # Only set when addressed=True; kept separate so queries can filter on
    # the boolean without parsing datetimes
    addressed_at: datetime | None = None
