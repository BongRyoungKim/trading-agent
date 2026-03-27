"""
Market hours gating — optional time-based trading window filter.

Crypto markets are 24/7, so this feature is disabled by default.
Enable when trading instruments with fixed hours (e.g., futures, stock CFDs)
or to avoid high-volatility low-liquidity windows.

Usage:
    config = MarketHoursConfig(
        enabled=True,
        trading_hours="09:00-18:00",  # UTC
        trading_days="mon-fri",
    )
    if config.is_trading_time():
        engine.tick(symbol)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time


_DAY_ABBR = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_RANGE_RE = re.compile(r"^(\d{1,2}:\d{2})-(\d{1,2}:\d{2})$")


@dataclass(frozen=True)
class MarketHoursConfig:
    """
    Immutable trading-hours configuration.

    Args:
        enabled:       Whether to apply time restrictions (default False = trade 24/7).
        trading_hours: "HH:MM-HH:MM" range in UTC (default "00:00-23:59").
        trading_days:  Weekday range "mon-fri" or comma list "mon,wed,fri"
                       (default "mon-sun").
    """

    enabled: bool = False
    trading_hours: str = "00:00-23:59"
    trading_days: str = "mon-sun"

    def __post_init__(self) -> None:
        if self.enabled:
            self._parse_hours()       # validate on construction
            self._parse_days()

    # ── Public API ────────────────────────────────────────────────────────────

    def is_trading_time(self, dt: datetime | None = None) -> bool:
        """
        Return True if *dt* (default: now UTC) falls within the trading window.
        Always returns True when enabled=False.
        """
        if not self.enabled:
            return True

        now = dt if dt is not None else datetime.now(UTC)
        if not self._is_trading_day(now.weekday()):
            return False

        start, end = self._parse_hours()
        current = now.time().replace(tzinfo=None)
        return start <= current <= end

    def open_time(self) -> time:
        """Return the configured open time (HH:MM)."""
        start, _ = self._parse_hours()
        return start

    def close_time(self) -> time:
        """Return the configured close time (HH:MM)."""
        _, end = self._parse_hours()
        return end

    # ── Internal ──────────────────────────────────────────────────────────────

    def _parse_hours(self) -> tuple[time, time]:
        m = _RANGE_RE.match(self.trading_hours.strip())
        if not m:
            raise ValueError(
                f"Invalid trading_hours format '{self.trading_hours}'. "
                "Expected 'HH:MM-HH:MM' (e.g. '09:00-18:00')."
            )
        start = _parse_time(m.group(1))
        end = _parse_time(m.group(2))
        if start >= end:
            raise ValueError(
                f"trading_hours start ({m.group(1)}) must be before end ({m.group(2)})."
            )
        return start, end

    def _parse_days(self) -> set[int]:
        raw = self.trading_days.strip().lower()
        # Range form: "mon-fri"
        if "-" in raw and "," not in raw:
            parts = raw.split("-", 1)
            if len(parts) == 2 and parts[0] in _DAY_ABBR and parts[1] in _DAY_ABBR:
                start_day = _DAY_ABBR[parts[0]]
                end_day = _DAY_ABBR[parts[1]]
                if start_day <= end_day:
                    return set(range(start_day, end_day + 1))
                # wrap-around (e.g. "fri-mon")
                return set(range(start_day, 7)) | set(range(0, end_day + 1))

        # Comma-separated form: "mon,wed,fri"
        days: set[int] = set()
        for token in raw.split(","):
            token = token.strip()
            if token not in _DAY_ABBR:
                raise ValueError(
                    f"Unknown day abbreviation '{token}'. "
                    f"Valid: {list(_DAY_ABBR.keys())}"
                )
            days.add(_DAY_ABBR[token])
        if not days:
            raise ValueError("trading_days cannot be empty.")
        return days

    def _is_trading_day(self, weekday: int) -> bool:
        return weekday in self._parse_days()


def _parse_time(s: str) -> time:
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time '{s}'. Expected HH:MM.")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Time value out of range: {s}")
    return time(h, m)


def market_hours_from_settings(settings: object) -> MarketHoursConfig:
    """
    Build MarketHoursConfig from a Settings-like object.
    Gracefully falls back to defaults for any missing attribute.
    Strictly casts values to prevent mock objects from leaking through.
    """
    raw_enabled = getattr(settings, "market_hours_enabled", False)
    # Only activate if the attribute is literally True (not a MagicMock / truthy object)
    enabled = raw_enabled is True or raw_enabled == "true" or raw_enabled == "True"

    raw_hours = getattr(settings, "trading_hours", "00:00-23:59")
    trading_hours = raw_hours if isinstance(raw_hours, str) else "00:00-23:59"

    raw_days = getattr(settings, "trading_days", "mon-sun")
    trading_days = raw_days if isinstance(raw_days, str) else "mon-sun"

    return MarketHoursConfig(
        enabled=enabled,
        trading_hours=trading_hours,
        trading_days=trading_days,
    )
