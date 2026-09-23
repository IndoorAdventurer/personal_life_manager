"""Tests for plm.timeutil — local-time "today" and current-week helpers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from plm import timeutil


@pytest.fixture(autouse=True)
def _amsterdam(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLM_TIMEZONE", "Europe/Amsterdam")


class TestLocalTz:
    def test_reads_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PLM_TIMEZONE", "America/New_York")
        assert timeutil.local_tz().key == "America/New_York"

    def test_empty_env_var_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # docker-compose passes ${PLM_TIMEZONE:-} through as "" when unset
        monkeypatch.setenv("PLM_TIMEZONE", "")
        assert timeutil.local_tz().key == timeutil.DEFAULT_TIMEZONE

    def test_invalid_zone_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PLM_TIMEZONE", "Mars/Olympus_Mons")
        with pytest.raises(Exception):
            timeutil.local_tz()

    def test_local_now_is_aware_and_local(self) -> None:
        now = timeutil.local_now()
        assert now.tzinfo is not None
        assert now.tzinfo.key == "Europe/Amsterdam"  # type: ignore[union-attr]


class TestCrossesMidnight:
    """Between 00:00 and 02:00 CEST, UTC is still on the previous day."""

    # Monday 2026-09-21 00:30 CEST == Sunday 2026-09-20 22:30 UTC
    _UTC = datetime(2026, 9, 20, 22, 30, tzinfo=timezone.utc)

    def test_today_is_local_monday(self) -> None:
        local = self._UTC.astimezone(timeutil.local_tz())
        assert timeutil.today_weekday(local) == "monday"
        # Sanity: the UTC view would have said sunday (the old bug)
        assert timeutil.today_weekday(self._UTC) == "sunday"

    def test_week_is_local_new_week(self) -> None:
        local = self._UTC.astimezone(timeutil.local_tz())
        assert timeutil.current_week(local) == "2026-W39"
        assert timeutil.current_week(self._UTC) == "2026-W38"

    def test_defaults_use_local_now(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            timeutil, "local_now",
            lambda: self._UTC.astimezone(timeutil.local_tz()),
        )
        assert timeutil.today_weekday() == "monday"
        assert timeutil.current_week() == "2026-W39"
