"""
Composite Strategy: combines multiple strategies with a configurable voting rule.

Modes:
  "all"  — ALL sub-strategies must agree (conservative, fewer false positives).
  "any"  — ANY sub-strategy trigger is sufficient (aggressive, higher trade frequency).
  "majority" — More than half must agree (balanced).

The composite signal strength is the average of agreeing sub-strategy strengths.

Usage:
    from src.strategy.composite import CompositeStrategy
    from src.strategy.ma_crossover import MACrossoverStrategy
    from src.strategy.rsi_strategy import RSIStrategy

    strategy = CompositeStrategy(
        symbol="BTC/USDT",
        strategies=[
            MACrossoverStrategy(symbol="BTC/USDT", fast_period=20, slow_period=50),
            RSIStrategy(symbol="BTC/USDT"),
        ],
        mode="all",
    )
    signal = strategy.generate_signal(df)
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import pandas as pd

from src.strategy.base import BaseStrategy
from src.strategy.models import Signal, SignalAction
from src.strategy.registry import register
from src.utils.exceptions import StrategyError

VotingMode = Literal["all", "any", "majority"]


@register
class CompositeStrategy(BaseStrategy):
    """
    Combines multiple sub-strategies using a voting rule.

    Args:
        symbol:     Trading symbol.
        strategies: List of BaseStrategy instances to combine.
        mode:       Voting rule — "all", "any", or "majority".
    """

    def __init__(
        self,
        symbol: str,
        strategies: list[BaseStrategy],
        mode: VotingMode = "all",
    ) -> None:
        if not strategies:
            raise ValueError("CompositeStrategy requires at least one sub-strategy")
        if mode not in ("all", "any", "majority"):
            raise ValueError(f"mode must be 'all', 'any', or 'majority', got {mode!r}")
        self._symbol = symbol
        self._strategies = strategies
        self._mode = mode

    @property
    def name(self) -> str:
        names = "+".join(s.name for s in self._strategies)
        return f"composite_{self._mode}[{names}]"

    def min_required_bars(self) -> int:
        """Maximum of all sub-strategy minimums."""
        return max(s.min_required_bars() for s in self._strategies)

    def get_parameters(self) -> dict:
        return {
            "symbol": self._symbol,
            "mode": self._mode,
            "sub_strategies": [s.name for s in self._strategies],
        }

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        self.validate_data(data)

        signals = []
        for strategy in self._strategies:
            try:
                sig = strategy.generate_signal(data)
                signals.append(sig)
            except StrategyError:
                # Sub-strategy has insufficient data — treat as HOLD
                signals.append(self._hold_signal(data))

        timestamp = datetime.now(UTC)

        buy_signals = [s for s in signals if s.action == SignalAction.BUY]
        sell_signals = [s for s in signals if s.action == SignalAction.SELL]
        n = len(signals)

        if self._vote(len(buy_signals), n):
            return Signal(
                symbol=self._symbol,
                action=SignalAction.BUY,
                strength=sum(s.strength for s in buy_signals) / len(buy_signals),
                reason=f"{self._mode}-vote BUY: {len(buy_signals)}/{n} agree",
                timestamp=timestamp,
                metadata={"votes_buy": len(buy_signals), "total": n},
            )

        if self._vote(len(sell_signals), n):
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=sum(s.strength for s in sell_signals) / len(sell_signals),
                reason=f"{self._mode}-vote SELL: {len(sell_signals)}/{n} agree",
                timestamp=timestamp,
                metadata={"votes_sell": len(sell_signals), "total": n},
            )

        return Signal(
            symbol=self._symbol,
            action=SignalAction.HOLD,
            strength=0.0,
            reason=f"No {self._mode}-vote consensus",
            timestamp=timestamp,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _vote(self, agreeing: int, total: int) -> bool:
        if self._mode == "all":
            return agreeing == total
        if self._mode == "any":
            return agreeing >= 1
        # majority
        return agreeing > total / 2

    def _hold_signal(self, data: pd.DataFrame) -> Signal:
        return Signal(
            symbol=self._symbol,
            action=SignalAction.HOLD,
            strength=0.0,
            reason="Insufficient data",
            timestamp=datetime.now(UTC),
        )
