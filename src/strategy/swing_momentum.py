"""
Swing Momentum Strategy (15m)

진입: 중기 상승 추세 안에서 RSI 과매도 후 회복 구간을 매수.
청산: EMA 데스크로스(추세 붕괴) 또는 RSI 과매수(엔진 TP가 먼저 잡음).

기존 Scalping5mStrategy 문제점 분석 후 설계:
  - 3m 캔들 노이즈 → 15m으로 타임프레임 상향
  - EMA 크로스오버 과다 신호 → RSI 과매도 회복 패턴으로 전환
  - ADX 22로 느슨한 추세 필터 → ADX 28 이상 강한 추세만 진입
  - 거래량 1.5× 기준 부족 → 2.0× 이상 기관 참여 확인
  - EMA proximity 3% 너무 넓음 → 2.5% 이내 풀백 엔트리만 허용

기댓값(EV) 개선 목표:
  현재: WR 33% × W/L 0.76 → EV = -1,194 KRW/거래
  목표: WR 42%+ × W/L 1.5+ → EV > 0

BUY 조건 (전부 충족):
  1. EMA20 > EMA50 (15m 중기 상승 추세)
  2. ADX ≥ 28 (추세 강도 확인, 횡보 구간 배제)
  3. 최근 rsi_lookback 봉 안에 RSI < rsi_oversold (과매도 진입 확인)
  4. 현재 RSI: rsi_oversold ≤ RSI < rsi_max (회복 중, 과매수 아님)
  5. 가격이 EMA20에서 ema_proximity_pct% 이내 (풀백 진입, 추격 매수 금지)
  6. 거래량 ≥ vol_mult × 20봉 평균 (매수세 확인)
  7. MACD 히스토그램 > 0 이고 전봉 대비 상승 (모멘텀 방향 확인)

SELL 조건:
  1. EMA20이 EMA50 아래로 교차(데스크로스) → 추세 붕괴
  2. RSI ≥ rsi_overbought → 과매수 (엔진 TP가 먼저 잡을 가능성 높음)
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
class SwingMomentumStrategy(BaseStrategy):
    """
    15분봉 스윙 모멘텀 전략 — 상승 추세 안 RSI 과매도 회복 패턴 매수.

    Args:
        symbol:             거래 심볼.
        ema_fast:           단기 EMA 기간 (기본 20).
        ema_slow:           장기 EMA 기간 (기본 50).
        rsi_period:         RSI 기간 (기본 14).
        rsi_oversold:       과매도 기준 RSI — 이 값 아래를 찍어야 진입 허용 (기본 40).
        rsi_max:            진입 허용 RSI 상한 — 이 값 이상이면 추격 매수 금지 (기본 58).
        rsi_overbought:     SELL 신호 RSI 기준 (기본 72).
        adx_threshold:      최소 ADX — 이 값 미만 횡보 구간 진입 차단 (기본 28).
        vol_mult:           거래량이 20봉 평균의 이 배수 이상이어야 진입 (기본 2.0).
        ema_proximity_pct:  가격이 EMA fast에서 이 % 이내여야 진입 (기본 2.5).
        atr_period:         ATR 기간 — 손절가 계산용 메타데이터 (기본 14).
        macd_fast:          MACD 단기 EMA (기본 12).
        macd_slow:          MACD 장기 EMA (기본 26).
        macd_signal:        MACD 시그널 (기본 9).
        rsi_lookback:       RSI 과매도 체크 소급 봉 수 (기본 4).
    """

    def __init__(
        self,
        symbol: str,
        ema_fast: int = 20,
        ema_slow: int = 50,
        rsi_period: int = 14,
        rsi_oversold: float = 40.0,
        rsi_max: float = 58.0,
        rsi_overbought: float = 72.0,
        adx_threshold: float = 28.0,
        vol_mult: float = 2.0,
        ema_proximity_pct: float = 2.5,
        atr_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        rsi_lookback: int = 4,
    ) -> None:
        if ema_fast >= ema_slow:
            raise ValueError(f"ema_fast ({ema_fast}) must be < ema_slow ({ema_slow})")
        if not (0.0 < rsi_oversold < rsi_max < rsi_overbought <= 100.0):
            raise ValueError(
                f"RSI 범위 오류: rsi_oversold={rsi_oversold}, "
                f"rsi_max={rsi_max}, rsi_overbought={rsi_overbought}"
            )

        self._symbol = symbol
        self._ema_fast = ema_fast
        self._ema_slow = ema_slow
        self._rsi_period = rsi_period
        self._rsi_oversold = rsi_oversold
        self._rsi_max = rsi_max
        self._rsi_overbought = rsi_overbought
        self._adx_threshold = adx_threshold
        self._vol_mult = vol_mult
        self._vol_lookback = 20
        self._ema_proximity_pct = ema_proximity_pct
        self._atr_period = atr_period
        self._macd_fast = macd_fast
        self._macd_slow = macd_slow
        self._macd_signal = macd_signal
        self._rsi_lookback = rsi_lookback

    @property
    def name(self) -> str:
        return f"swing_momentum_{self._ema_fast}_{self._ema_slow}_{self._symbol}"

    @property
    def timeframe(self) -> str:
        return "15m"

    def min_required_bars(self) -> int:
        return max(
            self._ema_slow * 2,
            self._macd_slow * 2,
            self._atr_period + 1,
            self._vol_lookback + 1,
            self._rsi_period + self._rsi_lookback + 1,
        )

    def get_parameters(self) -> dict:
        return {
            "symbol": self._symbol,
            "ema_fast": self._ema_fast,
            "ema_slow": self._ema_slow,
            "rsi_period": self._rsi_period,
            "rsi_oversold": self._rsi_oversold,
            "rsi_max": self._rsi_max,
            "rsi_overbought": self._rsi_overbought,
            "adx_threshold": self._adx_threshold,
            "vol_mult": self._vol_mult,
            "ema_proximity_pct": self._ema_proximity_pct,
            "atr_period": self._atr_period,
            "macd_fast": self._macd_fast,
            "macd_slow": self._macd_slow,
            "macd_signal": self._macd_signal,
            "rsi_lookback": self._rsi_lookback,
        }

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        self.validate_data(data)

        close = data["close"]
        high  = data["high"]
        low   = data["low"]
        volume = data["volume"]
        timestamp = (
            data["timestamp"].iloc[-1]
            if "timestamp" in data.columns
            else datetime.now(UTC)
        )

        # ── 지표 계산 ──────────────────────────────────────────────────────────
        ema_f      = ema(close, self._ema_fast)
        ema_s      = ema(close, self._ema_slow)
        rsi_series = calc_rsi(close, self._rsi_period)
        macd_df    = macd(close, self._macd_fast, self._macd_slow, self._macd_signal)
        atr_series = atr(high, low, close, self._atr_period)
        adx_series = calc_adx(high, low, close)
        # shift(1): 현재 봉을 제외한 이전 vol_lookback개 봉의 평균 — 현재 봉을
        # 자기 자신의 기준선에 포함시키면 급증분이 평균을 같이 끌어올려
        # vol_ratio가 실제보다 낮게 계산되는 문제가 있었음.
        vol_avg    = volume.shift(1).rolling(self._vol_lookback).mean()

        # 현재값
        price_now    = float(close.iloc[-1])
        ema_f_now    = float(ema_f.iloc[-1])
        ema_s_now    = float(ema_s.iloc[-1])
        ema_f_prev   = float(ema_f.iloc[-2])
        ema_s_prev   = float(ema_s.iloc[-2])
        rsi_now      = float(rsi_series.iloc[-1])
        hist_now     = float(macd_df["histogram"].iloc[-1])
        hist_prev    = float(macd_df["histogram"].iloc[-2])
        atr_now      = float(atr_series.iloc[-1])
        adx_now      = float(adx_series.iloc[-1])
        vol_now      = float(volume.iloc[-1])
        vol_avg_now  = float(vol_avg.iloc[-1])

        # ── 크로스 감지 ────────────────────────────────────────────────────────
        # 데스크로스: 전봉 EMA fast ≥ slow → 현재봉 fast < slow
        death_cross = ema_f_prev >= ema_s_prev and ema_f_now < ema_s_now

        # ── 진입 조건 ──────────────────────────────────────────────────────────
        uptrend = ema_f_now > ema_s_now
        adx_ok  = adx_now >= self._adx_threshold

        # 풀백 엔트리: 가격이 EMA fast에서 ema_proximity_pct% 이내
        proximity_pct = (
            abs(price_now - ema_f_now) / ema_f_now * 100
            if ema_f_now > 0 else 999.0
        )
        ema_proximity_ok = proximity_pct <= self._ema_proximity_pct

        # 거래량 급증
        vol_ratio = vol_now / vol_avg_now if vol_avg_now > 0 else 0.0
        vol_ok    = vol_ratio >= self._vol_mult

        # MACD 히스토그램: 양수이고 전봉 대비 상승 (모멘텀 확인)
        macd_ok = hist_now > 0 and hist_now > hist_prev

        # RSI: 최근 rsi_lookback봉 안에 과매도 구간 진입했다 회복
        recent_rsi      = rsi_series.iloc[-self._rsi_lookback :]
        rsi_was_oversold = bool((recent_rsi < self._rsi_oversold).any())
        rsi_in_range     = self._rsi_oversold <= rsi_now < self._rsi_max

        # ── 메타데이터 ─────────────────────────────────────────────────────────
        meta = {
            "price":         price_now,
            "ema_fast":      round(ema_f_now, 4),
            "ema_slow":      round(ema_s_now, 4),
            "rsi":           round(rsi_now, 1),
            "macd_hist":     round(hist_now, 4),
            "atr":           round(atr_now, 4),
            "adx":           round(adx_now, 1),
            "vol_ratio":     round(vol_ratio, 2),
            "proximity_pct": round(proximity_pct, 2),
            "cond": {
                "uptrend":          uptrend,
                "adx_ok":           adx_ok,
                "ema_proximity_ok": ema_proximity_ok,
                "vol_ok":           vol_ok,
                "macd_ok":          macd_ok,
                "rsi_was_oversold": rsi_was_oversold,
                "rsi_in_range":     rsi_in_range,
            },
        }

        # ── SELL ①: 데스크로스 (추세 붕괴) ────────────────────────────────────
        if death_cross:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=1.0,
                reason=(
                    f"데스크로스: EMA{self._ema_fast}({ema_f_now:.2f}) < "
                    f"EMA{self._ema_slow}({ema_s_now:.2f})"
                ),
                timestamp=timestamp,
                metadata=meta,
            )

        # ── SELL ②: RSI 과매수 (엔진 TP가 먼저 잡을 가능성 높음) ──────────────
        if rsi_now >= self._rsi_overbought:
            return Signal(
                symbol=self._symbol,
                action=SignalAction.SELL,
                strength=0.8,
                reason=f"RSI 과매수: {rsi_now:.1f} ≥ {self._rsi_overbought}",
                timestamp=timestamp,
                metadata=meta,
            )

        # ── BUY: 상승 추세 안 RSI 과매도 회복 ────────────────────────────────
        buy_condition = (
            uptrend
            and adx_ok
            and ema_proximity_ok
            and vol_ok
            and macd_ok
            and rsi_was_oversold
            and rsi_in_range
        )
        if buy_condition:
            strength = (rsi_now - self._rsi_oversold) / max(
                self._rsi_max - self._rsi_oversold, 1.0
            )
            return Signal(
                symbol=self._symbol,
                action=SignalAction.BUY,
                strength=max(0.5, min(1.0, strength)),
                reason=(
                    f"추세 풀백 매수: EMA{self._ema_fast}>{self._ema_slow} | "
                    f"RSI 회복 {rsi_now:.1f} | "
                    f"MACD+{hist_now:.4f} | "
                    f"ADX={adx_now:.1f} | "
                    f"Vol={vol_ratio:.1f}× | "
                    f"근접={proximity_pct:.2f}%"
                ),
                timestamp=timestamp,
                metadata=meta,
            )

        # ── HOLD: 미충족 조건 로깅 ─────────────────────────────────────────────
        missing: list[str] = []
        if not uptrend:
            missing.append(
                f"하락추세(EMA{self._ema_fast}={ema_f_now:.2f}<EMA{self._ema_slow}={ema_s_now:.2f})"
            )
        if not adx_ok:
            missing.append(f"ADX={adx_now:.1f}<{self._adx_threshold}(횡보)")
        if not ema_proximity_ok:
            missing.append(f"근접={proximity_pct:.2f}%>{self._ema_proximity_pct}%(추격금지)")
        if not vol_ok:
            missing.append(f"Vol={vol_ratio:.1f}×<{self._vol_mult}×")
        if not macd_ok:
            missing.append(f"MACD_hist={hist_now:.4f}(모멘텀부족)")
        if not rsi_was_oversold:
            rsi_min_recent = float(recent_rsi.min())
            missing.append(f"RSI최저={rsi_min_recent:.1f}>{self._rsi_oversold}(과매도없음)")
        if not rsi_in_range:
            missing.append(f"RSI={rsi_now:.1f}(범위:{self._rsi_oversold}-{self._rsi_max})")

        return Signal(
            symbol=self._symbol,
            action=SignalAction.HOLD,
            strength=0.0,
            reason=f"신호없음: {', '.join(missing) if missing else '조건미충족'}",
            timestamp=timestamp,
            metadata=meta,
        )
