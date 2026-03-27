"""
Moving Average Crossover Strategy.

BUY  signal: fast MA crosses above slow MA (golden cross).
SELL signal: fast MA crosses below slow MA (death cross).
HOLD       : no crossover in most recent bar.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from src.strategy.base import BaseStrategy
from src.strategy.models import Signal, SignalAction
from src.strategy.registry import register
from src.utils.indicators import ema, sma


@register
class MACrossoverStrategy(BaseStrategy):
    """
    Moving Average Crossover.

    Args:
        fast_period: Period of the fast moving average (default 20).
        slow_period: Period of the slow moving average (default 50).
        ma_type:     'sma' or 'ema' (default 'ema').
        symbol:      Trading symbol this strategy targets.
    """

    def __init__(
        self,
        symbol: str,
        fast_period: int = 20,
        slow_period: int = 50,
        ma_type: str = "ema",
    ) -> None:
        if fast_period >= slow_period:
            raise ValueError(
                f"fast_period ({fast_period}) must be < slow_period ({slow_period})"
            )
        self._symbol = symbol
        self._fast = fast_period
        self._slow = slow_period
        self._ma_type = ma_type

    @property
    def name(self) -> str:
        return f"ma_crossover_{self._ma_type}_{self._fast}_{self._slow}"

    def min_required_bars(self) -> int:
        return self._slow + 1  # need at least one bar after slow MA warms up

    def get_parameters(self) -> dict:
        return {
            "symbol": self._symbol,
            "fast_period": self._fast,
            "slow_period": self._slow,
            "ma_type": self._ma_type,
        }

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        self.validate_data(data)

        close = data["close"]
        ma_fn = ema if self._ma_type == "ema" else sma

        fast_ma = ma_fn(close, self._fast)
        slow_ma = ma_fn(close, self._slow)

        # Most recent and previous values
        fast_now = fast_ma.iloc[-1]
        fast_prev = fast_ma.iloc[-2]
        slow_now = slow_ma.iloc[-1]
        slow_prev = slow_ma.iloc[-2]

        timestamp = (
            data["timestamp"].iloc[-1]
            if "timestamp" in data.columns
            else datetime.now(UTC)
        )

        # Golden cross: fast crossed above slow
        if fast_prev <= slow_prev and fast_now > slow_now:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.BUY,
                strength=min(1.0, abs(fast_now - slow_now) / slow_now * 100),
                reason=f"{self._ma_type.upper()}({self._fast}) crossed above {self._ma_type.upper()}({self._slow})",
                timestamp=timestamp,
                metadata={
                    f"fast_ma": round(fast_now, 4),
                    f"slow_ma": round(slow_now, 4),
                },
            )

        # Death cross: fast crossed below slow
        if fast_prev >= slow_prev and fast_now < slow_now:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=min(1.0, abs(slow_now - fast_now) / slow_now * 100),
                reason=f"{self._ma_type.upper()}({self._fast}) crossed below {self._ma_type.upper()}({self._slow})",
                timestamp=timestamp,
                metadata={
                    f"fast_ma": round(fast_now, 4),
                    f"slow_ma": round(slow_now, 4),
                },
            )

        return Signal(
            symbol=self._symbol,
            action=SignalAction.HOLD,
            strength=0.0,
            reason="No crossover",
            timestamp=timestamp,
        )
