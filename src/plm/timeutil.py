"""Local-time helpers shared by the web UI and the MCP server.

Time blocks store a weekday + "HH:MM" with no timezone — they are implicitly in
the user's local time.  Anything that relates "now" to those blocks (today's
column, the current week) must therefore use local time too, not UTC; otherwise
the wrong day/week is picked between midnight local and midnight UTC.

Timestamps (created_at, updated_at, …) stay in UTC — they are unaffected.

Environment variables:
  PLM_TIMEZONE — IANA zone name, e.g. "Europe/Amsterdam" (optional, default below)
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Europe/Amsterdam"


def local_tz() -> ZoneInfo:
    """The user's timezone, from PLM_TIMEZONE.

    Read on every call (not cached at import) so tests can monkeypatch the env
    var.  An invalid name raises ZoneInfoNotFoundError — fail loudly rather than
    silently fall back to UTC and reintroduce the bug this module fixes.
    """
    return ZoneInfo(os.environ.get("PLM_TIMEZONE") or DEFAULT_TIMEZONE)


def local_now() -> datetime:
    """Current time as a timezone-aware datetime in the user's timezone."""
    return datetime.now(local_tz())


def current_week(now: datetime | None = None) -> str:
    """ISO week string for *now* (default: local_now()), e.g. '2026-W10'."""
    cal = (now or local_now()).isocalendar()
    return f"{cal.year}-W{cal.week:02d}"


def today_weekday(now: datetime | None = None) -> str:
    """Lower-case weekday name for *now* (default: local_now()), e.g. 'monday'."""
    return (now or local_now()).strftime("%A").lower()
