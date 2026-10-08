"""
VPIN(Volume-Synchronized Probability of Informed Trading, 근사) + 방향성
거래량 델타 신호 — 순수 계산 로직, 네트워크 I/O 없음.

2026-10-08 리서치(scripts/vpin_funding_backtest.py 계열, 12개 종목 ·
8개월 구간별 분해 6구간 중 4구간 플러스)와 정확히 동일한 공식.

주의: 교과서적 VPIN은 "동일 거래량 버킷"으로 나누는 게 정석이나, 여기서는
연속된 15분봉 N개를 거래량 가중치로 근사했다(시간버킷 근사 — 리서치
단계부터 이렇게 단순화했고, 이후 정교화하지 않는다. 재현성 우선).
"""
from __future__ import annotations

import pandas as pd

WINDOW = 40          # 2026-10-08 리서치에서 검증된 기본값
QUANTILE = 0.9
VPIN_THRESHOLD_LOOKBACK = 200
MIN_BARS_REQUIRED = VPIN_THRESHOLD_LOOKBACK + WINDOW  # 임계값 계산용 워밍업 포함


def compute_vpin_features(binance_df: pd.DataFrame, window: int = WINDOW) -> pd.DataFrame:
    """binance_df: columns=[timestamp, volume, delta](delta=taker_buy-taker_sell).
    미래 데이터를 참조하지 않는 rolling 계산만 사용(룩어헤드 없음)."""
    df = binance_df.copy()
    total = df["volume"].rolling(window).sum()
    imbalance = df["delta"].abs().rolling(window).sum()
    df["vpin"] = imbalance / total.replace(0, pd.NA)
    df["vpin_threshold"] = df["vpin"].rolling(VPIN_THRESHOLD_LOOKBACK, min_periods=50).quantile(QUANTILE)
    df["net_delta_window"] = df["delta"].rolling(window).sum()
    return df


def latest_signal(binance_df: pd.DataFrame, window: int = WINDOW) -> bool | None:
    """가장 최근 봉의 '정보성 매수 흡수' 신호. 워밍업 부족 시 None."""
    row = latest_row(binance_df, window)
    if row is None:
        return None
    return bool(row["vpin"] > row["vpin_threshold"] and row["net_delta_window"] > 0)


def latest_row(binance_df: pd.DataFrame, window: int = WINDOW) -> pd.Series | None:
    """가장 최근 봉의 전체 피처 행(vpin 수치 포함) — 여러 종목이 동시에
    신호를 낼 때 vpin 크기로 우선순위를 매기는 용도(scanner.py). 워밍업
    부족 시 None."""
    if len(binance_df) < MIN_BARS_REQUIRED:
        return None
    featured = compute_vpin_features(binance_df, window)
    last = featured.iloc[-1]
    if pd.isna(last["vpin"]) or pd.isna(last["vpin_threshold"]) or pd.isna(last["net_delta_window"]):
        return None
    return last


__all__ = ["WINDOW", "QUANTILE", "MIN_BARS_REQUIRED", "compute_vpin_features", "latest_signal", "latest_row"]
