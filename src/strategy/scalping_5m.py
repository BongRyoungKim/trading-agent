"""
5-Minute EMA Scalping Strategy.

Short-term scalping on 5-minute candles using EMA crossover as the primary
trend signal, MACD histogram for momentum confirmation, and RSI to filter
overbought entries.

Signal logic:
  BUY  : EMA9 crosses above EMA21 (golden cross)
         AND MACD histogram turns positive (momentum confirms)
         AND RSI is between rsi_min and overbought (not extended)
  SELL : EMA9 crosses below EMA21 (death cross)
         OR RSI >= overbought (take profit before reversal)
         OR MACD histogram turns negative while holding
  HOLD : All other conditions
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from src.strategy.base import BaseStrategy
from src.strategy.models import Signal, SignalAction
from src.strategy.registry import register
from src.utils.indicators import atr, ema, macd, rsi as calc_rsi


@register
class Scalping5mStrategy(BaseStrategy):
    """
    5-Minute EMA Scalping Strategy.

    Args:
        symbol:        Trading symbol.
        ema_fast:      Fast EMA period (default 9).
        ema_slow:      Slow EMA period (default 21).
        rsi_period:    RSI lookback period (default 7 — faster for scalping).
        rsi_min:       Minimum RSI for entry, avoids weak momentum (default 45).
        overbought:    RSI level for take-profit SELL and entry block (default 75).
        atr_period:    ATR period for stop-loss metadata (default 14).
    """

    def __init__(
        self,
        symbol: str,
        ema_fast: int = 5,
        ema_slow: int = 13,
        rsi_period: int = 6,
        rsi_min: float = 25.0,
        overbought: float = 80.0,
        atr_period: int = 14,
    ) -> None:
        if ema_fast >= ema_slow:
            raise ValueError(f"ema_fast ({ema_fast}) must be < ema_slow ({ema_slow})")
        if not 0.0 < rsi_min < overbought <= 100.0:
            raise ValueError(f"rsi_min={rsi_min}, overbought={overbought} invalid range")

        self._symbol = symbol
        self._ema_fast = ema_fast
        self._ema_slow = ema_slow
        self._rsi_period = rsi_period
        self._rsi_min = rsi_min
        self._overbought = overbought
        self._atr_period = atr_period

    @property
    def name(self) -> str:
        return f"scalping_5m_ema{self._ema_fast}_{self._ema_slow}_{self._symbol}"

    @property
    def timeframe(self) -> str:
        return "3m"

    def min_required_bars(self) -> int:
        # EMA slow needs ~2x period for warmup; MACD needs 26+9; pick max
        return max(self._ema_slow * 2, 26, self._atr_period + 1)

    def get_parameters(self) -> dict:
        return {
            "symbol": self._symbol,
            "ema_fast": self._ema_fast,
            "ema_slow": self._ema_slow,
            "rsi_period": self._rsi_period,
            "rsi_min": self._rsi_min,
            "overbought": self._overbought,
            "atr_period": self._atr_period,
        }

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        self.validate_data(data)

        close = data["close"]
        timestamp = (
            data["timestamp"].iloc[-1]
            if "timestamp" in data.columns
            else datetime.now(UTC)
        )

        # ── Indicators ────────────────────────────────────────────────────────
        ema_f = ema(close, self._ema_fast)
        ema_s = ema(close, self._ema_slow)
        macd_df = macd(close)
        rsi_series = calc_rsi(close, self._rsi_period)

        ema_f_now = float(ema_f.iloc[-1])
        ema_f_prev = float(ema_f.iloc[-2])
        ema_s_now = float(ema_s.iloc[-1])
        ema_s_prev = float(ema_s.iloc[-2])

        hist_now = float(macd_df["histogram"].iloc[-1])
        hist_prev = float(macd_df["histogram"].iloc[-2])

        rsi_now = float(rsi_series.iloc[-1])

        atr_val = float(atr(data["high"], data["low"], close, self._atr_period).iloc[-1])

        golden_cross = ema_f_prev <= ema_s_prev and ema_f_now > ema_s_now
        death_cross = ema_f_prev >= ema_s_prev and ema_f_now < ema_s_now
        above_ema = ema_f_now > ema_s_now
        macd_turned_positive = hist_now > 0  # relaxed: histogram positive (not just turn)
        macd_turned_negative = hist_prev >= 0 > hist_now

        current_price = float(close.iloc[-1])
        meta = {
            "price": current_price,
            "ema_fast": round(ema_f_now, 2),
            "ema_slow": round(ema_s_now, 2),
            "macd_hist": round(hist_now, 4),
            "rsi": round(rsi_now, 2),
            "atr": round(atr_val, 2),
        }

        # ── SELL ①: Death cross (primary exit) ────────────────────────────────
        if death_cross:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=1.0,
                reason=f"EMA{self._ema_fast} crossed below EMA{self._ema_slow} (death cross)",
                timestamp=timestamp,
                metadata=meta,
            )

        # ── SELL ②: Overbought RSI (take profit) ──────────────────────────────
        if rsi_now >= self._overbought:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=0.9,
                reason=f"RSI({self._rsi_period})={rsi_now:.1f} overbought, taking profit",
                timestamp=timestamp,
                metadata=meta,
            )

        # ── SELL ③: MACD histogram turns negative while above EMA (momentum loss) ─
        if macd_turned_negative and above_ema:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=0.7,
                reason=f"MACD histogram turned negative (hist={hist_now:.4f}), momentum fading",
                timestamp=timestamp,
                metadata=meta,
            )

        # ── BUY: EMA9 > EMA21 (bullish) + MACD histogram turns positive + RSI in range
        # Condition A: golden cross this candle + MACD already positive
        # Condition B: already above EMA + MACD just turned positive
        buy_condition = (
            above_ema
            and macd_turned_positive
            and self._rsi_min <= rsi_now < self._overbought
        )
        if buy_condition:
            trigger = "golden cross + " if golden_cross else ""
            strength = min(1.0, (rsi_now - self._rsi_min) / (self._overbought - self._rsi_min))
            return Signal(
                symbol=self._symbol,
                action=SignalAction.BUY,
                strength=max(0.5, strength),
                reason=(
                    f"{trigger}EMA{self._ema_fast} > EMA{self._ema_slow} + "
                    f"MACD hist turned positive ({hist_now:.4f}) + RSI={rsi_now:.1f}"
                ),
                timestamp=timestamp,
                metadata=meta,
            )

        return Signal(
            symbol=self._symbol,
            action=SignalAction.HOLD,
            strength=0.0,
            reason=f"No signal: EMA_diff={ema_f_now - ema_s_now:.2f}, RSI={rsi_now:.1f}, MACD_hist={hist_now:.4f}",
            timestamp=timestamp,
            metadata=meta,
        )
