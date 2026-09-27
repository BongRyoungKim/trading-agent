"""
다변량 ML 신호(RandomForest)가 쓰는 피처 계산 — 순수 함수, 네트워크 I/O 없음.

2026-09-27 리서치(27개 종목, 6구간 워크포워드 — 전 구간에서 "아무 날에나
사서 5일 보유" 기준선 대비 우위 확인)에서 검증된 11개 피처와 정확히 동일한
공식. 피처를 더 추가하거나(요일, 상대순위 등) 손절 조건을 더한 버전은 전부
검증 결과가 나빠져서 기각됐다 — 이 단순한 11개 구성을 그대로 유지한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLS = [
    "rsi14", "rsi5", "adx14", "ema_ratio", "vol_ratio",
    "ret_5d", "ret_10d", "ret_20d", "atr_pct", "dist_20d_high", "dist_20d_low",
]
HOLD_DAYS = 5
MIN_BARS_REQUIRED = 60  # 20일 롤링 윈도우 + 여유분


def _rsi(close: pd.Series, period: int) -> pd.Series:
    # 학습에 쓰인 리서치 스크립트(scripts/ml_multivariate_backtest.py 계열,
    # 2026-09-27)와 공식을 정확히 동일하게 유지 — 학습/추론 피처 불일치를
    # 막기 위해 이 함수는 절대 "개선"하지 않는다. loss==0인 구간(순수
    # 상승만 있는 완전 이례적 경우)은 rs가 NaN이 되어 그 날의 신호는
    # latest_feature_row()가 안전하게 건너뛴다 — 실거래에서는 사실상
    # 발생하지 않는 극단값이라 학습 때와 동일하게 그대로 둔다.
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(period).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.rolling(period).mean()


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    OHLCV DataFrame(오름차순 timestamp, 컬럼: open/high/low/close/volume)을
    받아 FEATURE_COLS 전부를 계산해 붙여 반환한다. 각 피처는 shift/rolling만
    사용해 미래 데이터를 참조하지 않는다 (룩어헤드 없음).
    """
    df = df.copy()
    df["rsi14"] = _rsi(df["close"], 14)
    df["rsi5"] = _rsi(df["close"], 5)
    df["adx14"] = _adx(df, 14)
    ema20 = df["close"].ewm(span=20).mean()
    ema50 = df["close"].ewm(span=50).mean()
    df["ema_ratio"] = ema20 / ema50 - 1
    df["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
    df["ret_5d"] = df["close"].pct_change(5)
    df["ret_10d"] = df["close"].pct_change(10)
    df["ret_20d"] = df["close"].pct_change(20)
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    df["atr_pct"] = tr.rolling(14).mean() / df["close"]
    df["dist_20d_high"] = df["close"] / df["high"].rolling(20).max() - 1
    df["dist_20d_low"] = df["close"] / df["low"].rolling(20).min() - 1
    return df


def latest_feature_row(df: pd.DataFrame) -> pd.Series | None:
    """가장 최근(마지막) 행의 피처 벡터를 반환. 워밍업 기간이 부족해 NaN이
    섞여 있으면 None(아직 신호 계산 불가)."""
    if len(df) < MIN_BARS_REQUIRED:
        return None
    featured = compute_features(df)
    last = featured.iloc[-1]
    if last[FEATURE_COLS].isna().any():
        return None
    return last


__all__ = ["FEATURE_COLS", "HOLD_DAYS", "MIN_BARS_REQUIRED", "compute_features", "latest_feature_row"]
