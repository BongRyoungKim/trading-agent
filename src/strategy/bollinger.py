"""
Bollinger Band Strategy.

BUY  signal: Price closes below lower band (oversold squeeze).
SELL signal: Price closes above upper band (overbought squeeze).
HOLD       : Price is within bands.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from src.strategy.base import BaseStrategy
from src.strategy.models import Signal, SignalAction
from src.strategy.registry import register
from src.utils.indicators import bollinger_bands


@register
class BollingerBandStrategy(BaseStrategy):
    """
    Bollinger Band Mean-Reversion Strategy.

    Args:
        symbol:    Trading symbol.
        period:    Moving average period (default 20).
        std_dev:   Standard deviation multiplier (default 2.0).
    """

    def __init__(
        self,
        symbol: str,
        period: int = 20,
        std_dev: float = 2.0,
    ) -> None:
        self._symbol = symbol
        self._period = period
        self._std_dev = std_dev

    @property
    def name(self) -> str:
        return f"bollinger_{self._period}_{self._std_dev}"

    def min_required_bars(self) -> int:
        return self._period + 1

    def get_parameters(self) -> dict:
        return {
            "symbol": self._symbol,
            "period": self._period,
            "std_dev": self._std_dev,
        }

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        self.validate_data(data)

        close = data["close"]
        bb = bollinger_bands(close, self._period, self._std_dev)

        last_close = close.iloc[-1]
        upper = bb["upper"].iloc[-1]
        lower = bb["lower"].iloc[-1]
        middle = bb["middle"].iloc[-1]

        timestamp = (
            data["timestamp"].iloc[-1]
            if "timestamp" in data.columns
            else datetime.now(UTC)
        )

        band_width = upper - lower

        # Price closed below lower band → potential reversal up
        if last_close < lower:
            distance = (lower - last_close) / band_width if band_width > 0 else 0.0
            return Signal(
                symbol=self._symbol,
                action=SignalAction.BUY,
                strength=min(1.0, distance),
                reason=f"Price {last_close:.2f} below lower band {lower:.2f}",
                timestamp=timestamp,
                metadata={
                    "close": round(last_close, 4),
                    "bb_lower": round(lower, 4),
                    "bb_upper": round(upper, 4),
                    "bb_middle": round(middle, 4),
                },
            )

        # Price closed above upper band → potential reversal down
        if last_close > upper:
            distance = (last_close - upper) / band_width if band_width > 0 else 0.0
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=min(1.0, distance),
                reason=f"Price {last_close:.2f} above upper band {upper:.2f}",
                timestamp=timestamp,
                metadata={
                    "close": round(last_close, 4),
                    "bb_lower": round(lower, 4),
                    "bb_upper": round(upper, 4),
                    "bb_middle": round(middle, 4),
                },
            )

        return Signal(
            symbol=self._symbol,
            action=SignalAction.HOLD,
            strength=0.0,
            reason=f"Price {last_close:.2f} within bands [{lower:.2f}, {upper:.2f}]",
            timestamp=timestamp,
        )
