"""
RSI Mean Reversion Strategy (15m)

하락장/횡보장 전용 — 과매도 패닉 매도 후 반등 포착.

설계 근거:
  SwingMomentumStrategy는 상승추세(EMA 골든크로스) 전제 조건으로
  2026년 1-4월 하락장에서 90일간 0 신호 발생. 하락/횡보장에서는
  추세 필터 없이 순수 RSI 과매도 반등 패턴을 활용.

BUY 조건 (전부 충족):
  1. RSI(rsi_fast) < rsi_oversold_fast  — 단기 RSI 극도 과매도
  2. RSI(14) < rsi_oversold_slow        — 중기 RSI도 과매도 확인
  3. 거래량 >= vol_mult × 20봉 평균    — 패닉 매도 거래량 급증
  4. 현재 봉이 양봉 (close > open)     — 반등 시작 캔들 확인
  5. 가격 >= price_sma200 × sma_floor  — 초강세 하락추세 회피 (선택적)

SELL 조건:
  1. RSI(14) >= rsi_exit               — 중립 구간 회복 (TP 기대)
  2. RSI(rsi_fast) >= rsi_exit_fast    — 단기 과매수 전환

엔진의 ATR 기반 SL/TP가 주 청산 역할 담당.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from src.strategy.base import BaseStrategy
from src.strategy.models import Signal, SignalAction
from src.strategy.registry import register
from src.utils.indicators import atr, ema, macd as calc_macd, rsi as calc_rsi


@register
class MeanReversionStrategy(BaseStrategy):
    """
    15분봉 RSI 과매도 반등 전략.

    Args:
        symbol:              거래 심볼.
        rsi_fast_period:     단기 RSI 기간 (기본 5). 빠른 과매도 감지.
        rsi_slow_period:     중기 RSI 기간 (기본 14). 추세 확인.
        rsi_oversold_fast:   단기 RSI 과매도 기준 (기본 25).
        rsi_oversold_slow:   중기 RSI 과매도 기준 (기본 35).
        rsi_exit:            중기 RSI SELL 기준 (기본 60).
        rsi_exit_fast:       단기 RSI SELL 기준 (기본 70).
        vol_mult:            거래량 급증 배수 기준 (기본 1.5).
        atr_period:          ATR 기간 — 엔진 SL/TP 메타 (기본 14).
        sma_period:          추세 필터 SMA 기간 (기본 200). 0이면 비활성화.
        sma_floor:           가격이 SMA × 이 배수 이상이어야 진입 (기본 0.85).
                             초강세 하락장 (SMA 대비 -15% 이상 하락) 진입 차단.
    """

    def __init__(
        self,
        symbol: str,
        rsi_fast_period: int = 5,
        rsi_slow_period: int = 14,
        rsi_oversold_fast: float = 25.0,
        rsi_oversold_slow: float = 35.0,
        rsi_exit: float = 60.0,
        rsi_exit_fast: float = 70.0,
        vol_mult: float = 1.5,
        atr_period: int = 14,
        sma_period: int = 0,
        sma_floor: float = 0.85,
        no_entry_hours_utc: list | None = None,
    ) -> None:
        if not (0 < rsi_oversold_fast < rsi_oversold_slow < rsi_exit <= 100):
            raise ValueError(
                f"RSI 범위 오류: fast={rsi_oversold_fast}, "
                f"slow={rsi_oversold_slow}, exit={rsi_exit}"
            )
        self._symbol = symbol
        self._rsi_fast_period = rsi_fast_period
        self._rsi_slow_period = rsi_slow_period
        self._rsi_oversold_fast = rsi_oversold_fast
        self._rsi_oversold_slow = rsi_oversold_slow
        self._rsi_exit = rsi_exit
        self._rsi_exit_fast = rsi_exit_fast
        self._vol_mult = vol_mult
        self._vol_lookback = 20
        self._atr_period = atr_period
        self._sma_period = sma_period
        self._sma_floor = sma_floor
        # [0,1,2] = 09-11 KST (worst performing window from backanalysis)
        self._no_entry_hours_utc: frozenset[int] = frozenset(no_entry_hours_utc or [])

    @property
    def name(self) -> str:
        return f"mean_reversion_{self._rsi_fast_period}_{self._rsi_slow_period}_{self._symbol}"

    @property
    def timeframe(self) -> str:
        return "15m"

    def min_required_bars(self) -> int:
        return max(
            self._sma_period + 1 if self._sma_period > 0 else 0,
            self._rsi_slow_period + 2,
            self._rsi_fast_period + 2,
            self._vol_lookback + 1,
            self._atr_period + 1,
        )

    def get_parameters(self) -> dict:
        return {
            "symbol":            self._symbol,
            "rsi_fast_period":   self._rsi_fast_period,
            "rsi_slow_period":   self._rsi_slow_period,
            "rsi_oversold_fast": self._rsi_oversold_fast,
            "rsi_oversold_slow": self._rsi_oversold_slow,
            "rsi_exit":          self._rsi_exit,
            "rsi_exit_fast":     self._rsi_exit_fast,
            "vol_mult":          self._vol_mult,
            "sma_period":        self._sma_period,
            "sma_floor":         self._sma_floor,
            "atr_period":        self._atr_period,
        }

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        self.validate_data(data)

        close  = data["close"]
        high   = data["high"]
        low    = data["low"]
        open_  = data["open"]
        volume = data["volume"]
        timestamp = (
            data["timestamp"].iloc[-1]
            if "timestamp" in data.columns
            else datetime.now(UTC)
        )

        # ── 지표 계산 ──────────────────────────────────────────────────────────
        rsi_fast   = calc_rsi(close, self._rsi_fast_period)
        rsi_slow   = calc_rsi(close, self._rsi_slow_period)
        atr_series = atr(high, low, close, self._atr_period)
        macd_df    = calc_macd(close)
        # shift(1): 현재 봉을 제외한 이전 vol_lookback개 봉의 평균 — 현재 봉을
        # 자기 자신의 기준선에 포함시키면 급증분이 평균을 같이 끌어올려
        # vol_ratio가 실제보다 낮게 계산되는 문제가 있었음.
        vol_avg    = volume.shift(1).rolling(self._vol_lookback).mean()

        rsi_f_now  = float(rsi_fast.iloc[-1])
        rsi_s_now  = float(rsi_slow.iloc[-1])
        atr_now    = float(atr_series.iloc[-1])
        vol_now    = float(volume.iloc[-1])
        vol_avg_now= float(vol_avg.iloc[-1])
        price_now  = float(close.iloc[-1])
        open_now   = float(open_.iloc[-1])

        hist_raw      = float(macd_df["histogram"].iloc[-1])
        hist_prev_raw = float(macd_df["histogram"].iloc[-2])
        macd_hist      = None if (hist_raw != hist_raw) else round(hist_raw, 4)        # NaN guard
        macd_hist_prev = None if (hist_prev_raw != hist_prev_raw) else round(hist_prev_raw, 4)

        # Block entry when selling pressure is accelerating (histogram falling)
        macd_momentum_ok = (
            macd_hist is None
            or macd_hist_prev is None
            or macd_hist >= macd_hist_prev
        )

        vol_ratio  = vol_now / vol_avg_now if vol_avg_now > 0 else 0.0
        is_bullish_candle = bool(close.iloc[-1] > open_.iloc[-1])

        # SMA 필터 (초강세 하락장 회피)
        sma_ok = True
        sma_now = None
        if self._sma_period > 0 and len(close) >= self._sma_period:
            sma_now = float(close.rolling(self._sma_period).mean().iloc[-1])
            sma_ok = bool(price_now >= sma_now * self._sma_floor)

        # ── 메타데이터 ─────────────────────────────────────────────────────────
        meta = {
            "price":     price_now,
            "rsi":       round(rsi_s_now, 1),   # 대시보드 공통 필드 (RSI14)
            "rsi_fast":  round(rsi_f_now, 1),
            "rsi_slow":  round(rsi_s_now, 1),
            "macd_hist": macd_hist,              # 대시보드 공통 필드
            "vol_krw":   round(vol_now * price_now, 0),  # 대시보드 공통 필드
            "atr":       round(atr_now, 4),
            "vol_ratio": round(vol_ratio, 2),
            "sma200":    round(sma_now, 2) if sma_now else None,
            "cond": {
                # 대시보드 공통 키 — 템플릿이 기대하는 필드명으로 매핑
                "above_ema":        is_bullish_candle,                              # E: 양봉(반등 시작)
                "macd_just_pos":    bool(macd_hist is not None and macd_hist > 0),  # M: MACD 양수 구간
                "macd_rising":      macd_momentum_ok,                               # MACD 모멘텀 개선
                "rsi_ok":           bool(rsi_s_now < self._rsi_oversold_slow),      # R: RSI14 과매도
                "vol_ok":           bool(vol_ratio >= self._vol_mult),              # V: 거래량 급증
                "adx_ok":           sma_ok,                                         # A: SMA 필터 통과
                "ema_proximity_ok": bool(rsi_f_now < self._rsi_oversold_fast),      # P: RSI5 과매도
                "death_cross":      bool(rsi_s_now >= self._rsi_exit),              # D: RSI14 회복(청산)
                "macd_turned_neg":  bool(rsi_f_now >= self._rsi_exit_fast),         # M↓: RSI5 과매수
                "overbought":       bool(rsi_s_now >= self._rsi_exit or rsi_f_now >= self._rsi_exit_fast),
            },
        }

        # ── SELL: RSI 회복 ─────────────────────────────────────────────────────
        if rsi_s_now >= self._rsi_exit:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=0.9,
                reason=f"RSI 중기 회복: {rsi_s_now:.1f} >= {self._rsi_exit}",
                timestamp=timestamp,
                metadata=meta,
            )
        if rsi_f_now >= self._rsi_exit_fast:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=0.7,
                reason=f"RSI 단기 과매수: {rsi_f_now:.1f} >= {self._rsi_exit_fast}",
                timestamp=timestamp,
                metadata=meta,
            )

        # ── BUY: 과매도 반등 ───────────────────────────────────────────────────
        current_hour_utc = datetime.now(UTC).hour
        time_ok = current_hour_utc not in self._no_entry_hours_utc

        buy_condition = (
            rsi_f_now < self._rsi_oversold_fast
            and rsi_s_now < self._rsi_oversold_slow
            and vol_ratio >= self._vol_mult
            and is_bullish_candle
            and sma_ok
            and time_ok
            and macd_momentum_ok
        )
        if buy_condition:
            # 과매도 깊이 기반 강도 계산 (더 낮을수록 강한 신호)
            depth = (self._rsi_oversold_fast - rsi_f_now) / self._rsi_oversold_fast
            strength = max(0.5, min(1.0, 0.5 + depth))
            return Signal(
                symbol=self._symbol,
                action=SignalAction.BUY,
                strength=strength,
                reason=(
                    f"과매도 반등: RSI5={rsi_f_now:.1f}<{self._rsi_oversold_fast} | "
                    f"RSI14={rsi_s_now:.1f}<{self._rsi_oversold_slow} | "
                    f"Vol={vol_ratio:.1f}x | 양봉확인"
                ),
                timestamp=timestamp,
                metadata=meta,
            )

        # ── HOLD ───────────────────────────────────────────────────────────────
        missing: list[str] = []
        if rsi_f_now >= self._rsi_oversold_fast:
            missing.append(f"RSI5={rsi_f_now:.1f}>={self._rsi_oversold_fast}")
        if rsi_s_now >= self._rsi_oversold_slow:
            missing.append(f"RSI14={rsi_s_now:.1f}>={self._rsi_oversold_slow}")
        if vol_ratio < self._vol_mult:
            missing.append(f"Vol={vol_ratio:.1f}x<{self._vol_mult}x")
        if not is_bullish_candle:
            missing.append(f"음봉({open_now:.0f}->{price_now:.0f})")
        if not sma_ok:
            missing.append(f"SMA200이탈(가격={price_now:.0f},SMA={sma_now:.0f})")
        if not time_ok:
            missing.append(f"시간대차단({current_hour_utc}UTC)")
        if not macd_momentum_ok:
            missing.append(f"MACD역전({macd_hist_prev:.4f}→{macd_hist:.4f})")

        return Signal(
            symbol=self._symbol,
            action=SignalAction.HOLD,
            strength=0.0,
            reason=f"신호없음: {', '.join(missing) if missing else '조건미충족'}",
            timestamp=timestamp,
            metadata=meta,
        )
