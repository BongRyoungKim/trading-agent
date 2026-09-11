"""Unit tests for src/utils/quiet_hours.py."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.utils.quiet_hours import QuietHours, quiet_hours_from_settings


class TestDisabled:
    def test_disabled_never_quiet(self) -> None:
        qh = QuietHours(enabled=False, hours_utc="22:00-07:00")
        assert qh.is_quiet(datetime(2026, 1, 1, 2, 0, tzinfo=UTC)) is False

    def test_empty_hours_treated_as_disabled(self) -> None:
        qh = QuietHours(enabled=True, hours_utc="")
        assert qh.is_quiet(datetime(2026, 1, 1, 2, 0, tzinfo=UTC)) is False


class TestNonWraparoundRange:
    def test_inside_range_is_quiet(self) -> None:
        qh = QuietHours(enabled=True, hours_utc="09:00-18:00")
        assert qh.is_quiet(datetime(2026, 1, 1, 12, 0, tzinfo=UTC)) is True

    def test_outside_range_not_quiet(self) -> None:
        qh = QuietHours(enabled=True, hours_utc="09:00-18:00")
        assert qh.is_quiet(datetime(2026, 1, 1, 20, 0, tzinfo=UTC)) is False

    def test_boundary_start_is_quiet(self) -> None:
        qh = QuietHours(enabled=True, hours_utc="09:00-18:00")
        assert qh.is_quiet(datetime(2026, 1, 1, 9, 0, tzinfo=UTC)) is True

    def test_boundary_end_is_quiet(self) -> None:
        qh = QuietHours(enabled=True, hours_utc="09:00-18:00")
        assert qh.is_quiet(datetime(2026, 1, 1, 18, 0, tzinfo=UTC)) is True


class TestWraparoundRange:
    def test_late_night_is_quiet(self) -> None:
        qh = QuietHours(enabled=True, hours_utc="22:00-07:00")
        assert qh.is_quiet(datetime(2026, 1, 1, 23, 30, tzinfo=UTC)) is True

    def test_early_morning_is_quiet(self) -> None:
        qh = QuietHours(enabled=True, hours_utc="22:00-07:00")
        assert qh.is_quiet(datetime(2026, 1, 1, 5, 0, tzinfo=UTC)) is True

    def test_daytime_not_quiet(self) -> None:
        qh = QuietHours(enabled=True, hours_utc="22:00-07:00")
        assert qh.is_quiet(datetime(2026, 1, 1, 14, 0, tzinfo=UTC)) is False

    def test_defaults_to_now_when_no_arg(self) -> None:
        qh = QuietHours(enabled=False, hours_utc="22:00-07:00")
        # Should not raise, and disabled always returns False regardless of "now"
        assert qh.is_quiet() is False


class TestInvalidFormat:
    def test_invalid_format_raises_on_construction(self) -> None:
        with pytest.raises(ValueError):
            QuietHours(enabled=True, hours_utc="not-a-range")

    def test_invalid_format_ok_when_disabled(self) -> None:
        # Disabled config should not eagerly validate a malformed string
        qh = QuietHours(enabled=False, hours_utc="not-a-range")
        assert qh.is_quiet() is False


class TestFromSettings:
    def test_builds_disabled_when_empty(self) -> None:
        settings = type("S", (), {"telegram_quiet_hours_utc": ""})()
        qh = quiet_hours_from_settings(settings)
        assert qh.enabled is False

    def test_builds_enabled_when_configured(self) -> None:
        settings = type("S", (), {"telegram_quiet_hours_utc": "22:00-07:00"})()
        qh = quiet_hours_from_settings(settings)
        assert qh.enabled is True
        assert qh.is_quiet(datetime(2026, 1, 1, 23, 0, tzinfo=UTC)) is True

    def test_missing_attribute_falls_back_to_disabled(self) -> None:
        settings = type("S", (), {})()
        qh = quiet_hours_from_settings(settings)
        assert qh.enabled is False
