"""
OHLCV data-quality validation.

Guards the strategy layer against bad exchange data: missing columns,
too few rows, NaNs, non-positive prices, negative volume, out-of-order
timestamps, and stale (too old) data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd

_REQUIRED_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")
_PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")


@dataclass
class ValidationResult:
    """Outcome of a single `OHLCVValidator.validate()` call.

    Truthy iff `ok` is True, so callers can write `if not result: ...`.
    """

    ok: bool
    issues: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


class OHLCVValidator:
    """Validates OHLCV DataFrames before they reach a strategy.

    Args:
        max_age_seconds: If > 0 and the DataFrame has a DatetimeIndex,
            the last bar must be no older than this many seconds
            (relative to `now`, see `validate()`). 0 disables the check.
    """

    def __init__(self, max_age_seconds: int = 0) -> None:
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be >= 0")
        self._max_age_seconds = max_age_seconds

    def validate(
        self,
        df: pd.DataFrame,
        min_rows: int = 1,
        now: datetime | None = None,
    ) -> ValidationResult:
        issues: list[str] = []

        missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            issues.append(f"Missing required column(s): {', '.join(missing)}")
            # Nothing else can be safely checked without the columns.
            return ValidationResult(ok=False, issues=issues)

        if len(df) < min_rows:
            issues.append(f"Insufficient rows: got {len(df)}, need {min_rows}")

        for col in _PRICE_COLUMNS:
            if df[col].isna().any():
                issues.append(f"NaN values found in '{col}'")

        for col in _PRICE_COLUMNS:
            if (df[col] <= 0).any():
                issues.append(f"Non-positive value(s) found in '{col}'")

        if (df["volume"] < 0).any():
            issues.append("Negative volume found")

        if isinstance(df.index, pd.DatetimeIndex):
            if not df.index.is_monotonic_increasing or df.index.has_duplicates:
                issues.append("Timestamps are not strictly ascending")

            if self._max_age_seconds > 0 and len(df) > 0:
                check_now = now if now is not None else datetime.now(UTC)
                last_ts = df.index[-1]
                age_seconds = (check_now - last_ts).total_seconds()
                if age_seconds > self._max_age_seconds:
                    issues.append(
                        f"Stale data: last bar is {age_seconds:.0f}s old "
                        f"(max {self._max_age_seconds}s)"
                    )

        return ValidationResult(ok=len(issues) == 0, issues=issues)
