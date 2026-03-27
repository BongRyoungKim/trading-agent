"""
VWAP Strategy.

BUY  signal: close crosses above VWAP AND close > confirmation EMA.
SELL signal: close crosses below VWAP AND close < confirmation EMA.
HOLD       : no crossover, or VWAP/EMA not yet calculable.

The confirmation EMA filter reduces false signals in choppy markets.
Set ema_period=0 to disable the EMA confirmation.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from src.strategy.base import BaseStrategy
from src.strategy.models import Signal, SignalAction
from src.strategy.registry import register
from src.utils.exceptions import StrategyError
from src.utils.indicators import ema, vwap


@register
class VWAPStrategy(BaseStrategy):
    """
    VWAP crossover with optional EMA confirmation.

    Args:
        symbol:     Trading symbol.
        ema_period: EMA confirmation period (default 20). Set 0 to disable.
    """

    def __init__(
        self,
        symbol: str,
        ema_period: int = 20,
    ) -> None:
        if ema_period < 0:
            raise ValueError("ema_period must be >= 0")
        self._symbol = symbol
        self._ema_period = ema_period

    @property
    def name(self) -> str:
        return f"vwap_ema{self._ema_period}_{self._symbol}"

    def min_required_bars(self) -> int:
        return max(2, self._ema_period + 1) if self._ema_period > 0 else 2

    def get_parameters(self) -> dict:
        return {"symbol": self._symbol, "ema_period": self._ema_period}

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        self.validate_data(data)

        vwap_line = vwap(data["high"], data["low"], data["close"], data["volume"])
        close = data["close"]

        # Crossover detection: compare last two bars
        prev_close = close.iloc[-2]
        curr_close = close.iloc[-1]
        prev_vwap = vwap_line.iloc[-2]
        curr_vwap = vwap_line.iloc[-1]

        if curr_vwap == 0:
            return self._hold("VWAP is zero")

        # EMA confirmation
        ema_ok_buy = True
        ema_ok_sell = True
        if self._ema_period > 0:
            ema_line = ema(close, self._ema_period)
            curr_ema = ema_line.iloc[-1]
            ema_ok_buy = curr_close > curr_ema
            ema_ok_sell = curr_close < curr_ema

        crossed_above = prev_close <= prev_vwap and curr_close > curr_vwap
        crossed_below = prev_close >= prev_vwap and curr_close < curr_vwap

        # Signal strength: proportional to distance from VWAP
        distance_pct = abs(float(curr_close - curr_vwap) / float(curr_vwap))
        strength = min(1.0, distance_pct * 10)  # cap at 1.0

        if crossed_above and ema_ok_buy:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.BUY,
                strength=max(0.5, strength),
                reason=f"price crossed above VWAP ({float(curr_vwap):.2f})",
                timestamp=datetime.now(UTC),
            )

        if crossed_below and ema_ok_sell:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=max(0.5, strength),
                reason=f"price crossed below VWAP ({float(curr_vwap):.2f})",
                timestamp=datetime.now(UTC),
            )

        return self._hold("no VWAP crossover")

    def _hold(self, reason: str) -> Signal:
        return Signal(
            symbol=self._symbol,
            action=SignalAction.HOLD,
            strength=0.0,
            reason=reason,
            timestamp=datetime.now(UTC),
        )
