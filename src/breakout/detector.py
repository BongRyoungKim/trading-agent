"""
브레이크아웃 조기진입 순수 판별 로직.

네트워크 I/O 없이 OHLCV DataFrame 하나만 받아서 신호 유무를 판단한다
(다른 strategy 모듈들과 동일한 설계 원칙 — 로직과 I/O 분리로 네트워크 없이
단위 테스트 가능하게 함).

핵심 아이디어 (SKR/KRW·TT/KRW 사고 이후 설계):
  1. 최근 조용했던 "바닥 구간"이 있어야 한다 (base_range_max_pct 이내).
  2. 그 구간 고점을 거래량 급증과 함께 뚫고 올라가는 "지금 이 순간"을 잡는다.
  3. 바닥 저점 대비 이미 너무 많이 오른 상태(추격 매수)는 제외한다 —
     SKR가 이미 150% 오른 뒤에 사는 게 아니라, 막 오르기 시작한 초반
     구간에서만 진입을 허용하는 게 이 필터의 핵심이다.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BreakoutConfig:
    """브레이크아웃 판별 파라미터. 전부 기본값은 설계 검토 시 합의된 값."""

    base_window_bars: int = 48          # 바닥 구간 봉 개수 (5분봉 48개 = 4시간)
    base_range_max_pct: float = 8.0     # 바닥 구간 고가-저가 범위 상한(%)
    breakout_margin_pct: float = 1.5    # 바닥 고점을 이 %만큼 넘어야 돌파 인정
    vol_mult: float = 5.0               # 돌파 캔들 거래량 / 바닥 구간 평균 거래량 최소 배수
    max_move_from_base_low_pct: float = 20.0  # 바닥 저점 대비 상승폭 상한(%) — 추격 방지
    min_quote_volume_krw: float = 10_000_000_000.0  # 최소 24h 거래대금(원) — 유동성 하한

    def __post_init__(self) -> None:
        if self.base_window_bars < 2:
            raise ValueError("base_window_bars must be >= 2")
        if self.base_range_max_pct <= 0:
            raise ValueError("base_range_max_pct must be positive")
        if self.vol_mult <= 0:
            raise ValueError("vol_mult must be positive")


@dataclass(frozen=True)
class BreakoutSignal:
    """감지된 브레이크아웃 신호."""

    symbol: str
    base_high: float
    base_low: float
    breakout_price: float           # 돌파 캔들 종가
    volume_ratio: float             # 돌파 캔들 거래량 / 바닥 평균 거래량
    move_from_base_low_pct: float   # 바닥 저점 대비 현재 상승폭(%)
    base_range_pct: float           # 바닥 구간 고가-저가 범위(%)


def detect_breakout(
    df: pd.DataFrame,
    symbol: str,
    cfg: BreakoutConfig | None = None,
) -> BreakoutSignal | None:
    """
    OHLCV DataFrame(오래된 것 → 최신 순, 컬럼: open/high/low/close/volume)을 받아
    마지막 봉이 브레이크아웃 조건을 만족하면 BreakoutSignal을, 아니면 None을 반환한다.

    df는 최소 cfg.base_window_bars + 1개의 행이 있어야 한다
    (바닥 구간 base_window_bars개 + 돌파를 확인할 마지막 1개).
    """
    cfg = cfg or BreakoutConfig()

    if len(df) < cfg.base_window_bars + 1:
        return None

    base = df.iloc[-(cfg.base_window_bars + 1) : -1]
    current = df.iloc[-1]

    base_high = float(base["high"].max())
    base_low = float(base["low"].min())
    if base_low <= 0:
        return None

    # 1) 바닥 구간이 충분히 조용했는가
    base_range_pct = (base_high - base_low) / base_low * 100
    if base_range_pct > cfg.base_range_max_pct:
        return None

    current_close = float(current["close"])
    current_volume = float(current["volume"])

    # 2) 바닥 고점을 breakout_margin_pct 이상 뚫었는가
    breakout_threshold = base_high * (1 + cfg.breakout_margin_pct / 100)
    if current_close < breakout_threshold:
        return None

    # 3) 거래량이 진짜 매수세를 반영하는가 (노이즈 배제)
    base_avg_volume = float(base["volume"].mean())
    if base_avg_volume <= 0:
        return None
    volume_ratio = current_volume / base_avg_volume
    if volume_ratio < cfg.vol_mult:
        return None

    # 4) 추격 매수 방지 — 바닥 저점 대비 이미 너무 올랐으면 진입 금지
    move_from_base_low_pct = (current_close - base_low) / base_low * 100
    if move_from_base_low_pct > cfg.max_move_from_base_low_pct:
        return None

    return BreakoutSignal(
        symbol=symbol,
        base_high=base_high,
        base_low=base_low,
        breakout_price=current_close,
        volume_ratio=round(volume_ratio, 2),
        move_from_base_low_pct=round(move_from_base_low_pct, 2),
        base_range_pct=round(base_range_pct, 2),
    )


__all__ = ["BreakoutConfig", "BreakoutSignal", "detect_breakout"]
