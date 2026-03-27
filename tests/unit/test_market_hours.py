"""Unit tests for MarketHoursConfig."""
from __future__ import annotations

from datetime import UTC, datetime, time

import pytest

from src.utils.market_hours import MarketHoursConfig, market_hours_from_settings


def _dt(weekday: int, hour: int, minute: int = 0) -> datetime:
    """Build a UTC datetime for a specific weekday (0=Mon) and time."""
    # Use known dates: 2024-01-01 is a Monday
    from datetime import timedelta
    base = datetime(2024, 1, 1, tzinfo=UTC)  # Monday
    return base + timedelta(days=weekday, hours=hour, minutes=minute)


# ── Disabled (always open) ────────────────────────────────────────────────────

class TestDisabled:
    def test_always_returns_true_when_disabled(self) -> None:
        cfg = MarketHoursConfig(enabled=False)
        assert cfg.is_trading_time() is True

    def test_any_datetime_returns_true_when_disabled(self) -> None:
        cfg = MarketHoursConfig(enabled=False)
        saturday = _dt(5, 3, 30)  # Saturday 3:30 AM
        assert cfg.is_trading_time(saturday) is True

    def test_invalid_hours_ignored_when_disabled(self) -> None:
        # Should not raise even with nonsense hours when disabled
        cfg = MarketHoursConfig(enabled=False, trading_hours="00:00-23:59")
        assert cfg.is_trading_time() is True


# ── Trading hours (time range) ────────────────────────────────────────────────

class TestTradingHours:
    def _cfg(self, hours: str = "09:00-18:00") -> MarketHoursConfig:
        return MarketHoursConfig(enabled=True, trading_hours=hours, trading_days="mon-sun")

    def test_within_hours_returns_true(self) -> None:
        cfg = self._cfg("09:00-18:00")
        assert cfg.is_trading_time(_dt(0, 12, 0)) is True  # 12:00 UTC

    def test_before_open_returns_false(self) -> None:
        cfg = self._cfg("09:00-18:00")
        assert cfg.is_trading_time(_dt(0, 8, 59)) is False  # 08:59

    def test_after_close_returns_false(self) -> None:
        cfg = self._cfg("09:00-18:00")
        assert cfg.is_trading_time(_dt(0, 18, 1)) is False  # 18:01

    def test_boundary_open_time_inclusive(self) -> None:
        cfg = self._cfg("09:00-18:00")
        assert cfg.is_trading_time(_dt(0, 9, 0)) is True

    def test_boundary_close_time_inclusive(self) -> None:
        cfg = self._cfg("09:00-18:00")
        assert cfg.is_trading_time(_dt(0, 18, 0)) is True

    def test_open_time_property(self) -> None:
        cfg = self._cfg("09:30-17:00")
        assert cfg.open_time() == time(9, 30)

    def test_close_time_property(self) -> None:
        cfg = self._cfg("09:30-17:00")
        assert cfg.close_time() == time(17, 0)


# ── Trading days ──────────────────────────────────────────────────────────────

class TestTradingDays:
    def _cfg(self, days: str) -> MarketHoursConfig:
        return MarketHoursConfig(enabled=True, trading_hours="00:00-23:59", trading_days=days)

    def test_weekday_passes_mon_fri(self) -> None:
        cfg = self._cfg("mon-fri")
        assert cfg.is_trading_time(_dt(0, 12)) is True   # Monday
        assert cfg.is_trading_time(_dt(4, 12)) is True   # Friday

    def test_weekend_blocked_with_mon_fri(self) -> None:
        cfg = self._cfg("mon-fri")
        assert cfg.is_trading_time(_dt(5, 12)) is False  # Saturday
        assert cfg.is_trading_time(_dt(6, 12)) is False  # Sunday

    def test_comma_separated_days(self) -> None:
        cfg = self._cfg("mon,wed,fri")
        assert cfg.is_trading_time(_dt(0, 12)) is True   # Monday
        assert cfg.is_trading_time(_dt(2, 12)) is True   # Wednesday
        assert cfg.is_trading_time(_dt(4, 12)) is True   # Friday
        assert cfg.is_trading_time(_dt(1, 12)) is False  # Tuesday

    def test_all_days_mon_sun(self) -> None:
        cfg = self._cfg("mon-sun")
        for day in range(7):
            assert cfg.is_trading_time(_dt(day, 12)) is True

    def test_single_day_sat(self) -> None:
        cfg = self._cfg("sat")
        assert cfg.is_trading_time(_dt(5, 12)) is True   # Saturday
        assert cfg.is_trading_time(_dt(0, 12)) is False  # Monday


# ── Validation errors ─────────────────────────────────────────────────────────

class TestValidation:
    def test_invalid_hours_format_raises(self) -> None:
        with pytest.raises(ValueError, match="trading_hours"):
            MarketHoursConfig(enabled=True, trading_hours="09:00")

    def test_start_ge_end_raises(self) -> None:
        with pytest.raises(ValueError, match="before end"):
            MarketHoursConfig(enabled=True, trading_hours="18:00-09:00")

    def test_invalid_day_abbreviation_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown day"):
            MarketHoursConfig(enabled=True, trading_days="xyz", trading_hours="09:00-18:00")

    def test_start_equals_end_raises(self) -> None:
        with pytest.raises(ValueError, match="before end"):
            MarketHoursConfig(enabled=True, trading_hours="09:00-09:00")


# ── market_hours_from_settings ────────────────────────────────────────────────

class TestFromSettings:
    def test_disabled_by_default(self) -> None:
        class FakeSettings:
            market_hours_enabled = False

        cfg = market_hours_from_settings(FakeSettings())
        assert cfg.enabled is False
        assert cfg.is_trading_time() is True

    def test_enabled_with_custom_hours(self) -> None:
        class FakeSettings:
            market_hours_enabled = True
            trading_hours = "09:00-17:00"
            trading_days = "mon-fri"

        cfg = market_hours_from_settings(FakeSettings())
        assert cfg.enabled is True
        assert cfg.open_time() == time(9, 0)

    def test_missing_attributes_use_defaults(self) -> None:
        cfg = market_hours_from_settings(object())
        assert cfg.enabled is False
