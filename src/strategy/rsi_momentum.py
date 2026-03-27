"""
Ross Cameron RSI Momentum Strategy.

Ross Cameron (Warrior Trading) treats RSI as a momentum confirmation indicator,
not a mean-reversion tool. The key insight: buy when momentum is TURNING positive
(RSI crosses 50 from below), not when it's already extended.

Signal logic:
  BUY  : RSI crosses above momentum_threshold (default 50) from below
         AND volume surge confirms conviction (>= volume_mult * avg volume)
         AND RSI is NOT already overbought (< overbought_block)
  SELL : RSI crosses below momentum_threshold (momentum exhausted)
  SELL : RSI >= overbought (profit-taking before reversal)
  HOLD : All other conditions
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from src.strategy.base import BaseStrategy
from src.strategy.models import Signal, SignalAction
from src.strategy.registry import register
from src.utils.indicators import rsi as calc_rsi


@register
class RSIMomentumStrategy(BaseStrategy):
    """
    Ross Cameron RSI Momentum Strategy.

    Args:
        symbol:               Trading symbol.
        rsi_period:           RSI lookback period (default 14).
        momentum_threshold:   RSI level used as the momentum midline (default 50).
        overbought:           RSI level for profit-taking SELL and entry block (default 75).
        volume_mult:          Volume must be >= this multiple of the lookback average (default 1.5).
        volume_lookback:      Number of bars for average volume calculation (default 20).
    """

    def __init__(
        self,
        symbol: str,
        rsi_period: int = 14,
        momentum_threshold: float = 50.0,
        overbought: float = 75.0,
        volume_mult: float = 1.5,
        volume_lookback: int = 20,
    ) -> None:
        if rsi_period < 2:
            raise ValueError(f"rsi_period must be >= 2, got {rsi_period}")
        if not 0.0 < momentum_threshold < 100.0:
            raise ValueError(f"momentum_threshold must be between 0 and 100, got {momentum_threshold}")
        if not momentum_threshold < overbought <= 100.0:
            raise ValueError(
                f"overbought ({overbought}) must be > momentum_threshold ({momentum_threshold})"
            )
        if volume_mult <= 0:
            raise ValueError(f"volume_mult must be > 0, got {volume_mult}")
        if volume_lookback < 1:
            raise ValueError(f"volume_lookback must be >= 1, got {volume_lookback}")

        self._symbol = symbol
        self._rsi_period = rsi_period
        self._momentum_threshold = momentum_threshold
        self._overbought = overbought
        self._volume_mult = volume_mult
        self._volume_lookback = volume_lookback

    @property
    def name(self) -> str:
        return f"rsi_momentum_{self._rsi_period}_{self._symbol}"

    def min_required_bars(self) -> int:
        # RSI needs rsi_period * 2 bars for warmup; volume needs volume_lookback + 1
        return max(self._rsi_period * 2, self._volume_lookback + 1)

    def get_parameters(self) -> dict:
        return {
            "symbol": self._symbol,
            "rsi_period": self._rsi_period,
            "momentum_threshold": self._momentum_threshold,
            "overbought": self._overbought,
            "volume_mult": self._volume_mult,
            "volume_lookback": self._volume_lookback,
        }

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        self.validate_data(data)

        rsi_series = calc_rsi(data["close"], self._rsi_period)
        rsi_now = float(rsi_series.iloc[-1])
        rsi_prev = float(rsi_series.iloc[-2])

        timestamp = (
            data["timestamp"].iloc[-1]
            if "timestamp" in data.columns
            else datetime.now(UTC)
        )

        # ── Entry block: already overbought ───────────────────────────────────
        if rsi_now >= self._overbought and rsi_prev >= self._overbought:
            return self._hold(
                f"RSI={rsi_now:.1f} already overbought, new entry blocked",
                rsi_now,
                timestamp,
            )

        # ── Volume surge check ────────────────────────────────────────────────
        vol_now = float(data["volume"].iloc[-1])
        vol_avg = float(data["volume"].iloc[-(self._volume_lookback + 1):-1].mean())
        volume_surge = vol_avg > 0 and vol_now >= vol_avg * self._volume_mult

        # ── BUY: RSI crosses above momentum_threshold with volume confirmation ─
        crossed_up = rsi_prev < self._momentum_threshold <= rsi_now
        if crossed_up:
            if not volume_surge:
                return self._hold(
                    f"RSI crossed {self._momentum_threshold} but no volume surge "
                    f"(vol={vol_now:.0f}, need>={vol_avg * self._volume_mult:.0f})",
                    rsi_now,
                    timestamp,
                )
            strength = min(
                1.0,
                (rsi_now - self._momentum_threshold)
                / (self._overbought - self._momentum_threshold),
            )
            return Signal(
                symbol=self._symbol,
                action=SignalAction.BUY,
                strength=max(0.3, strength),
                reason=(
                    f"RSI({self._rsi_period}) crossed {self._momentum_threshold} "
                    f"with volume surge ({vol_now:.0f} >= {vol_avg * self._volume_mult:.0f})"
                ),
                timestamp=timestamp,
                metadata={"rsi": round(rsi_now, 2), "volume_ratio": round(vol_now / vol_avg, 2) if vol_avg else 0},
            )

        # ── SELL ①: RSI crosses below momentum_threshold (momentum exhausted) ─
        crossed_down = rsi_prev >= self._momentum_threshold > rsi_now
        if crossed_down:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=0.8,
                reason=f"RSI({self._rsi_period}) dropped below {self._momentum_threshold}, momentum exhausted",
                timestamp=timestamp,
                metadata={"rsi": round(rsi_now, 2)},
            )

        # ── SELL ②: RSI >= overbought (profit-taking) ─────────────────────────
        if rsi_now >= self._overbought:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=1.0,
                reason=f"RSI({self._rsi_period})={rsi_now:.1f} overbought, taking profit",
                timestamp=timestamp,
                metadata={"rsi": round(rsi_now, 2)},
            )

        return self._hold(
            f"RSI({self._rsi_period})={rsi_now:.1f} neutral (no cross)",
            rsi_now,
            timestamp,
        )

    def _hold(self, reason: str, rsi: float = 0.0, timestamp: datetime | None = None) -> Signal:
        return Signal(
            symbol=self._symbol,
            action=SignalAction.HOLD,
            strength=0.0,
            reason=reason,
            timestamp=timestamp or datetime.now(UTC),
            metadata={"rsi": round(rsi, 2)},
        )
