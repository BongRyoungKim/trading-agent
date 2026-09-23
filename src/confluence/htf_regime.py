"""
상위 타임프레임(1일봉/4시간봉) 상승추세(uptrend) 판정 — ADX+EMA 조합.

`src/strategy/regime_adaptive.py`의 국면판정 공식(ADX(14)>=25 AND
EMA20>EMA50)을 그대로 재사용하되, 다른 타임프레임의 OHLCV에 적용할 수
있도록 순수 함수로 분리했다(라이브 15분봉 전용인 원본과 달리 이건
1d/4h 어디에나 쓸 수 있음). 네트워크 I/O 없음 — 이미 조회된 OHLCV
DataFrame만 받는다(단위 테스트 용이, src/breakout/detector.py와 동일
설계 원칙).

2026-09-23 백테스트(multi_tf_confluence_backtest.py)와 파라미터 동일.
"""
from __future__ import annotations

import pandas as pd

from src.utils.indicators import adx as calc_adx, ema

ADX_THRESHOLD = 25.0
EMA_FAST = 20
EMA_SLOW = 50


def is_uptrend(df: pd.DataFrame) -> bool:
    """df: OHLCV DataFrame(오래된 것 -> 최신 순, columns: high/low/close).
    최소 EMA_SLOW+1개 행이 있어야 판정 가능 — 부족하면 보수적으로 False.
    """
    if len(df) < EMA_SLOW + 1:
        return False
    adx_series = calc_adx(df["high"], df["low"], df["close"], 14)
    ema_f = ema(df["close"], EMA_FAST)
    ema_s = ema(df["close"], EMA_SLOW)
    return bool(adx_series.iloc[-1] >= ADX_THRESHOLD and ema_f.iloc[-1] > ema_s.iloc[-1])


__all__ = ["is_uptrend", "ADX_THRESHOLD", "EMA_FAST", "EMA_SLOW"]
