"""
5-Minute EMA Scalping Strategy.

Short-term scalping on 3-minute candles using EMA crossover as the primary
trend signal, MACD histogram for momentum confirmation, RSI to filter
overbought entries, volume confirmation to avoid low-liquidity traps,
and ADX to block entries in ranging/choppy markets.

Signal logic:
  BUY  : EMA9 > EMA21 (bullish alignment)
         AND MACD histogram turned positive within last 3 bars (relaxed window)
         AND RSI between rsi_min(40) and overbought(70)
         AND volume >= vol_mult(1.5) × 20-bar average
         AND ADX >= adx_threshold(25) — trending market only
  SELL : EMA9 crosses below EMA21 (death cross)
         OR RSI >= overbought (take profit before reversal)
         OR MACD histogram turns negative while above EMA (momentum loss)
  HOLD : All other conditions

Parameter changes from v1 (2026-04-01):
  rsi_min   45 → 40   (widen entry window, capture more momentum)
  overbought 75 → 70  (earlier exit before RSI exhaustion)
  vol_mult  1.2 → 1.5 (stricter volume filter, reduce false breakouts)
  MACD      1-bar strict → 3-bar window (reduce whipsaw from single-bar noise)
  ADX       new — blocks entries in ranging markets (ADX < 25)
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from src.strategy.base import BaseStrategy
from src.strategy.models import Signal, SignalAction
from src.strategy.registry import register
from src.utils.indicators import adx as calc_adx
from src.utils.indicators import atr, ema, macd, rsi as calc_rsi


@register
class Scalping5mStrategy(BaseStrategy):
    """
    5-Minute EMA Scalping Strategy (v2).

    Args:
        symbol:         Trading symbol.
        ema_fast:       Fast EMA period (default 9).
        ema_slow:       Slow EMA period (default 21).
        rsi_period:     RSI lookback period (default 7).
        rsi_min:        Minimum RSI for entry (default 40).
        overbought:     RSI level for take-profit SELL and entry block (default 70).
        atr_period:     ATR period for stop-loss metadata (default 14).
        vol_mult:       Volume must exceed this multiple of 20-bar average (default 1.5).
        adx_period:     ADX period for trend-strength filter (default 14).
        adx_threshold:  Minimum ADX to allow BUY entries (default 25).
        macd_window:    Bars to look back for MACD histogram crossover (default 3).
    """

    def __init__(
        self,
        symbol: str,
        ema_fast: int = 9,
        ema_slow: int = 21,
        rsi_period: int = 7,
        rsi_min: float = 40.0,
        overbought: float = 70.0,
        atr_period: int = 14,
        vol_mult: float = 1.5,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        macd_window: int = 3,
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
        self._vol_mult = vol_mult
        self._vol_lookback = 20
        self._adx_period = adx_period
        self._adx_threshold = adx_threshold
        self._macd_window = macd_window

    @property
    def name(self) -> str:
        return f"scalping_5m_ema{self._ema_fast}_{self._ema_slow}_{self._symbol}"

    @property
    def timeframe(self) -> str:
        return "3m"

    def min_required_bars(self) -> int:
        return max(
            self._ema_slow * 2,
            26,
            self._atr_period + 1,
            self._vol_lookback + 1,
            self._adx_period * 2,
        )

    def get_parameters(self) -> dict:
        return {
            "symbol": self._symbol,
            "ema_fast": self._ema_fast,
            "ema_slow": self._ema_slow,
            "rsi_period": self._rsi_period,
            "rsi_min": self._rsi_min,
            "overbought": self._overbought,
            "atr_period": self._atr_period,
            "vol_mult": self._vol_mult,
            "adx_period": self._adx_period,
            "adx_threshold": self._adx_threshold,
            "macd_window": self._macd_window,
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
        ema_f      = ema(close, self._ema_fast)
        ema_s      = ema(close, self._ema_slow)
        macd_df    = macd(close)
        rsi_series = calc_rsi(close, self._rsi_period)
        adx_series = calc_adx(data["high"], data["low"], close, self._adx_period)

        ema_f_now  = float(ema_f.iloc[-1])
        ema_f_prev = float(ema_f.iloc[-2])
        ema_s_now  = float(ema_s.iloc[-1])
        ema_s_prev = float(ema_s.iloc[-2])

        hist_now  = float(macd_df["histogram"].iloc[-1])
        hist_prev = float(macd_df["histogram"].iloc[-2])

        # MACD window: collect last macd_window histogram values (oldest→newest)
        window_vals = [
            float(macd_df["histogram"].iloc[-i])
            for i in range(1, self._macd_window + 1)
        ]  # [hist_now, hist_prev, hist_prev2, ...]

        rsi_now = float(rsi_series.iloc[-1])
        adx_now = float(adx_series.iloc[-1])

        atr_val = float(atr(data["high"], data["low"], close, self._atr_period).iloc[-1])

        # ── Volume confirmation ───────────────────────────────────────────────
        vol_ok = True
        vol_now = 0.0
        vol_avg = 0.0
        if "volume" in data.columns and len(data) >= self._vol_lookback + 1:
            vol_now = float(data["volume"].iloc[-1])
            vol_avg = float(data["volume"].iloc[-self._vol_lookback - 1:-1].mean())
            vol_ok = vol_avg > 0 and vol_now >= self._vol_mult * vol_avg

        # ── Cross conditions ──────────────────────────────────────────────────
        golden_cross = ema_f_prev <= ema_s_prev and ema_f_now > ema_s_now
        death_cross  = ema_f_prev >= ema_s_prev and ema_f_now < ema_s_now
        above_ema    = ema_f_now > ema_s_now

        # MACD turned positive within the last macd_window bars:
        # current bar must be positive AND at least one prior bar in window was ≤ 0
        macd_recently_crossed = (
            hist_now > 0
            and any(h <= 0 for h in window_vals[1:])
        )
        macd_turned_neg = hist_prev >= 0 > hist_now

        # ADX trend filter
        adx_ok = adx_now >= self._adx_threshold

        current_price = float(close.iloc[-1])
        meta = {
            "price": current_price,
            "ema_fast": round(ema_f_now, 4),
            "ema_slow": round(ema_s_now, 4),
            "macd_hist": round(hist_now, 4),
            "rsi": round(rsi_now, 2),
            "atr": round(atr_val, 4),
            "adx": round(adx_now, 2),
            "vol_ratio": round(vol_now / vol_avg, 2) if vol_avg > 0 else None,
            "cond": {
                "above_ema":       above_ema,
                "macd_just_pos":   macd_recently_crossed,
                "macd_turned_neg": macd_turned_neg,
                "rsi_ok":          self._rsi_min <= rsi_now < self._overbought,
                "overbought":      rsi_now >= self._overbought,
                "vol_ok":          vol_ok,
                "death_cross":     death_cross,
                "golden_cross":    golden_cross,
                "adx_ok":          adx_ok,
            },
        }

        # ── SELL ①: Death cross (primary exit) ───────────────────────────────
        if death_cross:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=1.0,
                reason=f"EMA{self._ema_fast} crossed below EMA{self._ema_slow} (death cross)",
                timestamp=timestamp,
                metadata=meta,
            )

        # ── SELL ②: Overbought RSI (take profit) ─────────────────────────────
        if rsi_now >= self._overbought:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=0.9,
                reason=f"RSI({self._rsi_period})={rsi_now:.1f} overbought, taking profit",
                timestamp=timestamp,
                metadata=meta,
            )

        # ── SELL ③: MACD histogram turns negative while above EMA ─────────────
        if macd_turned_neg and above_ema:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=0.7,
                reason=f"MACD histogram turned negative ({hist_now:.4f}), momentum fading",
                timestamp=timestamp,
                metadata=meta,
            )

        # ── BUY: MACD 3-bar window + EMA alignment + RSI + volume + ADX ───────
        buy_condition = (
            above_ema
            and macd_recently_crossed
            and self._rsi_min <= rsi_now < self._overbought
            and vol_ok
            and adx_ok
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
                    f"MACD hist crossed positive ({hist_now:.4f}) + "
                    f"RSI={rsi_now:.1f} + ADX={adx_now:.1f}"
                ),
                timestamp=timestamp,
                metadata=meta,
            )

        return Signal(
            symbol=self._symbol,
            action=SignalAction.HOLD,
            strength=0.0,
            reason=(
                f"No signal: EMA_diff={ema_f_now - ema_s_now:.4f}, "
                f"RSI={rsi_now:.1f}, MACD_hist={hist_now:.4f}, ADX={adx_now:.1f}"
            ),
            timestamp=timestamp,
            metadata=meta,
        )
