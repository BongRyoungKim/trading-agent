"""
Quiet hours — optional time window that suppresses info-level notifications.

Unlike src/utils/market_hours.py (trading-hours gating), this supports a
wraparound range (e.g. "22:00-07:00" spanning midnight), which is the common
shape for a "don't page me overnight" window.

Usage:
    qh = QuietHours(enabled=True, hours_utc="22:00-07:00")
    if qh.is_quiet():
        # suppress info-level telegram notifications
        ...
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time

_RANGE_RE = re.compile(r"^(\d{1,2}:\d{2})-(\d{1,2}:\d{2})$")


@dataclass(frozen=True)
class QuietHours:
    """
    Immutable quiet-hours configuration.

    Args:
        enabled:   Whether the quiet window is active (default False).
        hours_utc: "HH:MM-HH:MM" range in UTC. Wraparound supported
                   (start > end means the range spans midnight).
    """

    enabled: bool = False
    hours_utc: str = ""

    def __post_init__(self) -> None:
        if self.enabled and self.hours_utc:
            self._parse()  # validate on construction

    def is_quiet(self, dt: datetime | None = None) -> bool:
        """Return True if *dt* (default: now UTC) falls within quiet hours."""
        if not self.enabled or not self.hours_utc:
            return False

        start, end = self._parse()
        now = dt if dt is not None else datetime.now(UTC)
        current = now.time().replace(tzinfo=None)

        if start <= end:
            return start <= current <= end
        # Wraparound (e.g. 22:00-07:00): quiet if at/after start OR at/before end.
        return current >= start or current <= end

    def _parse(self) -> tuple[time, time]:
        m = _RANGE_RE.match(self.hours_utc.strip())
        if not m:
            raise ValueError(
                f"Invalid telegram_quiet_hours_utc format '{self.hours_utc}'. "
                "Expected 'HH:MM-HH:MM' (e.g. '22:00-07:00')."
            )
        return _parse_time(m.group(1)), _parse_time(m.group(2))


def _parse_time(s: str) -> time:
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time '{s}'. Expected HH:MM.")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Time value out of range: {s}")
    return time(h, m)


def quiet_hours_from_settings(settings: object) -> QuietHours:
    """
    Build QuietHours from a Settings-like object.
    Gracefully falls back to disabled for any missing/empty attribute.
    """
    raw_hours = getattr(settings, "telegram_quiet_hours_utc", "")
    hours_utc = raw_hours if isinstance(raw_hours, str) else ""
    return QuietHours(enabled=bool(hours_utc), hours_utc=hours_utc)
