"""
RSI (Relative Strength Index) Strategy.

BUY  signal: RSI crosses above oversold threshold (default 30).
SELL signal: RSI crosses below overbought threshold (default 70).
HOLD       : RSI is in the neutral zone.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from src.strategy.base import BaseStrategy
from src.strategy.models import Signal, SignalAction
from src.strategy.registry import register
from src.utils.indicators import rsi as calc_rsi


@register
class RSIStrategy(BaseStrategy):
    """
    RSI Mean-Reversion Strategy.

    Args:
        symbol:          Trading symbol.
        period:          RSI lookback period (default 14).
        oversold:        RSI level below which to look for BUY (default 30).
        overbought:      RSI level above which to look for SELL (default 70).
    """

    def __init__(
        self,
        symbol: str,
        period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
    ) -> None:
        if oversold >= overbought:
            raise ValueError(
                f"oversold ({oversold}) must be < overbought ({overbought})"
            )
        self._symbol = symbol
        self._period = period
        self._oversold = oversold
        self._overbought = overbought

    @property
    def name(self) -> str:
        return f"rsi_{self._period}_{int(self._oversold)}_{int(self._overbought)}"

    def min_required_bars(self) -> int:
        return self._period * 2  # RSI needs extra bars for warmup

    def get_parameters(self) -> dict:
        return {
            "symbol": self._symbol,
            "period": self._period,
            "oversold": self._oversold,
            "overbought": self._overbought,
        }

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        self.validate_data(data)

        rsi_series = calc_rsi(data["close"], self._period)
        rsi_now = rsi_series.iloc[-1]
        rsi_prev = rsi_series.iloc[-2]

        timestamp = (
            data["timestamp"].iloc[-1]
            if "timestamp" in data.columns
            else datetime.now(UTC)
        )

        # BUY: RSI was below oversold and is now recovering
        if rsi_prev < self._oversold and rsi_now >= self._oversold:
            strength = (self._oversold - min(rsi_prev, rsi_now)) / self._oversold
            return Signal(
                symbol=self._symbol,
                action=SignalAction.BUY,
                strength=min(1.0, max(0.0, strength)),
                reason=f"RSI({self._period}) crossed above oversold {self._oversold}",
                timestamp=timestamp,
                metadata={"rsi": round(rsi_now, 2)},
            )

        # SELL: RSI was above overbought and is now falling
        if rsi_prev > self._overbought and rsi_now <= self._overbought:
            strength = (max(rsi_prev, rsi_now) - self._overbought) / (100 - self._overbought)
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=min(1.0, max(0.0, strength)),
                reason=f"RSI({self._period}) crossed below overbought {self._overbought}",
                timestamp=timestamp,
                metadata={"rsi": round(rsi_now, 2)},
            )

        return Signal(
            symbol=self._symbol,
            action=SignalAction.HOLD,
            strength=0.0,
            reason=f"RSI({self._period}) = {rsi_now:.1f} (neutral zone)",
            timestamp=timestamp,
            metadata={"rsi": round(rsi_now, 2)},
        )
