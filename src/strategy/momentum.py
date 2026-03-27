"""
Momentum Strategy based on Rate of Change (ROC).

ROC = (close - close[period bars ago]) / close[period bars ago] * 100

BUY  signal: ROC > buy_threshold  (positive momentum)
SELL signal: ROC < sell_threshold (negative momentum, typically negative)
HOLD       : ROC between thresholds

Optionally, an RSI filter can be applied to avoid overbought/oversold entries.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from src.strategy.base import BaseStrategy
from src.strategy.models import Signal, SignalAction
from src.strategy.registry import register
from src.utils.indicators import rsi


@register
class MomentumStrategy(BaseStrategy):
    """
    Rate-of-Change momentum with optional RSI filter.

    Args:
        symbol:         Trading symbol.
        period:         ROC lookback period (default 10).
        buy_threshold:  ROC % above which a BUY is signalled (default 2.0).
        sell_threshold: ROC % below which a SELL is signalled (default -2.0).
        rsi_period:     RSI period for filter (default 14). Set 0 to disable.
        rsi_overbought: RSI above this → skip BUY (default 70).
        rsi_oversold:   RSI below this → skip SELL (default 30).
    """

    def __init__(
        self,
        symbol: str,
        period: int = 10,
        buy_threshold: float = 2.0,
        sell_threshold: float = -2.0,
        rsi_period: int = 14,
        rsi_overbought: float = 70.0,
        rsi_oversold: float = 30.0,
    ) -> None:
        if period < 1:
            raise ValueError("period must be >= 1")
        if buy_threshold <= sell_threshold:
            raise ValueError("buy_threshold must be > sell_threshold")
        self._symbol = symbol
        self._period = period
        self._buy_threshold = buy_threshold
        self._sell_threshold = sell_threshold
        self._rsi_period = rsi_period
        self._rsi_overbought = rsi_overbought
        self._rsi_oversold = rsi_oversold

    @property
    def name(self) -> str:
        return f"momentum_roc{self._period}_{self._symbol}"

    def min_required_bars(self) -> int:
        rsi_bars = self._rsi_period + 1 if self._rsi_period > 0 else 0
        return max(self._period + 1, rsi_bars)

    def get_parameters(self) -> dict:
        return {
            "symbol": self._symbol,
            "period": self._period,
            "buy_threshold": self._buy_threshold,
            "sell_threshold": self._sell_threshold,
            "rsi_period": self._rsi_period,
            "rsi_overbought": self._rsi_overbought,
            "rsi_oversold": self._rsi_oversold,
        }

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        self.validate_data(data)

        close = data["close"]
        prev = close.iloc[-(self._period + 1)]
        curr = close.iloc[-1]

        if prev == 0:
            return self._hold("previous close is zero")

        roc = float((curr - prev) / prev * 100)

        # RSI filter
        if self._rsi_period > 0:
            rsi_series = rsi(close, self._rsi_period)
            curr_rsi = float(rsi_series.iloc[-1])
            if roc > self._buy_threshold and curr_rsi > self._rsi_overbought:
                return self._hold(f"ROC={roc:.2f}% BUY blocked: RSI overbought ({curr_rsi:.1f})")
            if roc < self._sell_threshold and curr_rsi < self._rsi_oversold:
                return self._hold(f"ROC={roc:.2f}% SELL blocked: RSI oversold ({curr_rsi:.1f})")

        strength = min(1.0, abs(roc) / max(abs(self._buy_threshold), abs(self._sell_threshold)) / 2)

        if roc > self._buy_threshold:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.BUY,
                strength=max(0.5, strength),
                reason=f"ROC={roc:.2f}% > buy threshold {self._buy_threshold}%",
                timestamp=datetime.now(UTC),
            )

        if roc < self._sell_threshold:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=max(0.5, strength),
                reason=f"ROC={roc:.2f}% < sell threshold {self._sell_threshold}%",
                timestamp=datetime.now(UTC),
            )

        return self._hold(f"ROC={roc:.2f}% within thresholds")

    def _hold(self, reason: str) -> Signal:
        return Signal(
            symbol=self._symbol,
            action=SignalAction.HOLD,
            strength=0.0,
            reason=reason,
            timestamp=datetime.now(UTC),
        )
