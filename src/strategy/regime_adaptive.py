"""
Regime-Adaptive Strategy: automatically switches between MeanReversion and
SwingMomentum based on the current market regime.

Regime detection (15m bars, ADX + EMA alignment):
  Uptrend:   ADX >= adx_trend_threshold AND EMA_fast > EMA_slow
  Downtrend: ADX >= adx_trend_threshold AND EMA_fast <= EMA_slow
  Ranging:   ADX < adx_trend_threshold

Strategy routing:
  Uptrend:             SwingMomentumStrategy  (trend-following pullback)
  Ranging / Downtrend: MeanReversionStrategy  (oversold bounce)
"""
from __future__ import annotations

from typing import Literal

import pandas as pd

from src.strategy.base import BaseStrategy
from src.strategy.mean_reversion import MeanReversionStrategy
from src.strategy.models import Signal, SignalAction
from src.strategy.registry import register
from src.strategy.swing_momentum import SwingMomentumStrategy
from src.utils.indicators import adx as calc_adx, ema

Regime = Literal["uptrend", "ranging", "downtrend"]


def _classify_regime(
    data: pd.DataFrame, adx_threshold: float, ema_fast: int, ema_slow: int,
) -> Regime:
    """
    Pure ADX+EMA regime classifier — extracted so it can be reused with
    different thresholds for a different timeframe (e.g. higher_tf_data)
    without duplicating the math. RegimeAdaptiveStrategy.detect_regime() is
    a thin wrapper around this using its own 15m-tuned instance thresholds.
    """
    close = data["close"]
    high  = data["high"]
    low   = data["low"]

    adx_now   = float(calc_adx(high, low, close).iloc[-1])
    ema_f_now = float(ema(close, ema_fast).iloc[-1])
    ema_s_now = float(ema(close, ema_slow).iloc[-1])

    if adx_now >= adx_threshold:
        return "uptrend" if ema_f_now > ema_s_now else "downtrend"
    return "ranging"


@register
class RegimeAdaptiveStrategy(BaseStrategy):
    """
    15분봉 시장 국면 자동 전환 전략.

    ADX + EMA 정렬로 국면을 감지하고 적합한 서브 전략으로 신호를 위임한다.

    Args:
        symbol:                거래 심볼.
        adx_trend_threshold:   ADX 추세 판단 기준 (기본 25). 이상이면 추세 국면.
        ema_fast:              국면 감지용 단기 EMA 기간 (기본 20).
        ema_slow:              국면 감지용 장기 EMA 기간 (기본 50).
        mr_vol_mult:           MeanReversion 거래량 배수 (기본 1.0).
        mr_rsi_oversold_fast:  MeanReversion 단기 RSI 과매도 기준 (기본 20.0).
        mr_rsi_oversold_slow:  MeanReversion 중기 RSI 과매도 기준 (기본 30.0).
        mr_rsi_exit:           MeanReversion RSI 청산 기준 (기본 60.0).
        mr_rsi_exit_fast:      MeanReversion 단기 RSI(rsi_fast) 청산 기준 (기본 70.0).
        mr_no_entry_hours_utc: MeanReversion 진입 차단 UTC 시간대 목록.
        mr_sma_period:         MeanReversion 장기추세 필터 SMA 기간 (기본 0=비활성).
                                그라인딩 하락장(완만하지만 지속적인 하락) 회피용 —
                                가격이 SMA×mr_sma_floor 아래로 이격되면 진입 차단.
        mr_sma_floor:          장기추세 필터 이격 허용 배수 (기본 0.85).
                                mr_sma_period=0이면 사용되지 않음.
        mr_exit_momentum_gate: MeanReversion RSI 단기 과매수 청산에 MACD
                                모멘텀 게이트 적용 여부 (기본 False).
        mr_htf_block_on_downtrend: DOWNTREND 국면일 때 상위 시간봉(예: 1시간봉,
                                generate_signal의 higher_tf_data)도 downtrend면
                                MeanReversion 진입을 보류할지 여부 (기본 False).
        htf_adx_threshold:     상위 시간봉(higher_tf_data) 국면판정 전용 ADX
                                기준 (기본 25.0, 15분봉 adx_trend_threshold와
                                동일 기본값이지만 완전히 독립적으로 설정 가능 —
                                같은 봉 개수라도 타임프레임에 따라 실제 시간
                                폭·ADX 분포가 달라 15분봉용 값이 그대로
                                맞는다는 보장이 없다).
        htf_ema_fast:          상위 시간봉 국면판정 전용 단기 EMA 기간 (기본 20).
        htf_ema_slow:          상위 시간봉 국면판정 전용 장기 EMA 기간 (기본 50).
        sm_vol_mult:           SwingMomentum 거래량 배수 (기본 1.5).
        sm_adx_threshold:      SwingMomentum 내부 추세 강도 기준 (기본 28.0).
        sm_rsi_oversold:       SwingMomentum 과매도 재진입 기준 — 최근 4봉 안에
                                RSI가 이 값 아래를 찍어야 진입 허용 (기본 40.0).
    """

    def __init__(
        self,
        symbol: str,
        adx_trend_threshold: float = 25.0,
        ema_fast: int = 20,
        ema_slow: int = 50,
        mr_vol_mult: float = 1.0,
        mr_rsi_oversold_fast: float = 20.0,
        mr_rsi_oversold_slow: float = 30.0,
        mr_rsi_exit: float = 55.0,
        mr_rsi_exit_fast: float = 70.0,
        mr_no_entry_hours_utc: list | None = None,
        mr_sma_period: int = 0,
        mr_sma_floor: float = 0.85,
        mr_exit_momentum_gate: bool = False,
        mr_htf_block_on_downtrend: bool = False,
        htf_adx_threshold: float = 25.0,
        htf_ema_fast: int = 20,
        htf_ema_slow: int = 50,
        sm_vol_mult: float = 1.5,
        sm_adx_threshold: float = 28.0,
        sm_rsi_oversold: float = 40.0,
    ) -> None:
        if ema_fast >= ema_slow:
            raise ValueError(f"ema_fast ({ema_fast}) must be < ema_slow ({ema_slow})")
        if adx_trend_threshold <= 0:
            raise ValueError(f"adx_trend_threshold must be positive, got {adx_trend_threshold}")

        self._symbol = symbol
        self._adx_trend_threshold = adx_trend_threshold
        self._ema_fast_period = ema_fast
        self._ema_slow_period = ema_slow
        self._htf_adx_threshold = htf_adx_threshold
        self._htf_ema_fast = htf_ema_fast
        self._htf_ema_slow = htf_ema_slow

        self._mean_reversion = MeanReversionStrategy(
            symbol=symbol,
            vol_mult=mr_vol_mult,
            rsi_oversold_fast=mr_rsi_oversold_fast,
            rsi_oversold_slow=mr_rsi_oversold_slow,
            rsi_exit=mr_rsi_exit,
            rsi_exit_fast=mr_rsi_exit_fast,
            no_entry_hours_utc=mr_no_entry_hours_utc,
            sma_period=mr_sma_period,
            sma_floor=mr_sma_floor,
            exit_momentum_gate=mr_exit_momentum_gate,
            htf_block_on_downtrend=mr_htf_block_on_downtrend,
        )
        self._swing_momentum = SwingMomentumStrategy(
            symbol=symbol,
            vol_mult=sm_vol_mult,
            adx_threshold=sm_adx_threshold,
            rsi_oversold=sm_rsi_oversold,
        )

    @property
    def name(self) -> str:
        return (
            f"regime_adaptive_{self._ema_fast_period}_{self._ema_slow_period}"
            f"_{self._symbol}"
        )

    @property
    def timeframe(self) -> str:
        return "15m"

    def min_required_bars(self) -> int:
        return max(
            self._mean_reversion.min_required_bars(),
            self._swing_momentum.min_required_bars(),
        )

    def get_parameters(self) -> dict:
        return {
            "symbol": self._symbol,
            "adx_trend_threshold": self._adx_trend_threshold,
            "ema_fast": self._ema_fast_period,
            "ema_slow": self._ema_slow_period,
            "htf_adx_threshold": self._htf_adx_threshold,
            "htf_ema_fast": self._htf_ema_fast,
            "htf_ema_slow": self._htf_ema_slow,
            "mean_reversion": self._mean_reversion.get_parameters(),
            "swing_momentum": self._swing_momentum.get_parameters(),
        }

    def detect_regime(self, data: pd.DataFrame) -> Regime:
        """Classify current market regime from OHLCV data (15m-tuned thresholds)."""
        return _classify_regime(
            data, self._adx_trend_threshold, self._ema_fast_period, self._ema_slow_period,
        )

    def generate_signal(
        self, data: pd.DataFrame, higher_tf_data: pd.DataFrame | None = None,
    ) -> Signal:
        self.validate_data(data)

        regime = self.detect_regime(data)

        if regime == "uptrend":
            inner = self._swing_momentum.generate_signal(data)
        elif regime == "downtrend":
            # 다중 시간봉 확인: DOWNTREND 국면에서만 상위 시간봉 국면을 계산해
            # 전달한다(RANGING 경로는 검증된 기존 동작을 그대로 유지).
            htf_regime = (
                _classify_regime(
                    higher_tf_data, self._htf_adx_threshold,
                    self._htf_ema_fast, self._htf_ema_slow,
                )
                if higher_tf_data is not None else None
            )
            inner = self._mean_reversion.generate_signal(data, htf_regime=htf_regime)
        else:
            inner = self._mean_reversion.generate_signal(data)

        metadata = dict(inner.metadata) if inner.metadata else {}
        metadata["regime"] = regime

        return Signal(
            symbol=inner.symbol,
            action=inner.action,
            strength=inner.strength,
            reason=f"[{regime.upper()}] {inner.reason}",
            timestamp=inner.timestamp,
            metadata=metadata,
        )
