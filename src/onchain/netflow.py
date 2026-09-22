"""
BTC 거래소 넷플로우(유입-유출) 조회 + z-score 계산.

CoinMetrics Community API(무료, 인증 불필요)를 쓴다 — 2026-09-22 리서치에서
FlowInExUSD/FlowOutExUSD가 일봉 기준 자산별 최초 관측일부터 현재까지 무료로
전체 제공됨을 직접 확인했다(유료 티어는 Glassnode $999/월, CryptoQuant $99/월로
이 프로젝트 자본 규모(~193만원) 대비 비현실적).

네트워크 I/O(fetch_netflow_history)와 순수 계산(compute_zscore)을 분리한다 —
src/news/sentiment.py와 동일 원칙(로직을 네트워크 없이 단위 테스트 가능하게).

백테스트(btc_onchain_netflow_backtest.py, 2026-09-22)와 완전히 동일한 z-score
정의: 최근 `lookback`일(당일 포함) 넷플로우의 평균/표준편차 기준.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import requests

COINMETRICS_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
DEFAULT_LOOKBACK_DAYS = 30


def fetch_netflow_history(
    days: int = 60,
    timeout: int = 10,
    asset: str = "btc",
) -> pd.DataFrame:
    """
    최근 `days`일 BTC 거래소 유입/유출액(USD)을 조회해 일자별 넷플로우
    DataFrame(columns: date, netflow)으로 반환한다.

    네트워크/API 오류는 호출자에게 그대로 전파한다(호출자가 "이전 값 유지"
    등 운영 정책을 결정 — 이 함수는 순수 조회만 담당).
    """
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    resp = requests.get(
        COINMETRICS_URL,
        params={
            "assets": asset,
            "metrics": "FlowInExUSD,FlowOutExUSD",
            "frequency": "1d",
            "start_time": start.strftime("%Y-%m-%d"),
            "end_time": end.strftime("%Y-%m-%d"),
            "page_size": 10000,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    rows = resp.json().get("data", [])
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["date", "netflow"])
    df["date"] = pd.to_datetime(df["time"]).dt.date
    df["netflow"] = df["FlowInExUSD"].astype(float) - df["FlowOutExUSD"].astype(float)
    return df[["date", "netflow"]].reset_index(drop=True)


def compute_zscore(df: pd.DataFrame, lookback: int = DEFAULT_LOOKBACK_DAYS) -> float | None:
    """
    가장 최근 행(오늘)의 넷플로우가 최근 `lookback`일(오늘 포함) 분포에서
    몇 표준편차 떨어져 있는지 반환. 데이터 부족(lookback 미만) 또는
    표준편차 0(전부 동일값)이면 판단 불가로 None.
    """
    if len(df) < lookback:
        return None
    window = df["netflow"].iloc[-lookback:]
    mean = window.mean()
    std = window.std()
    if std == 0 or pd.isna(std):
        return None
    latest = df["netflow"].iloc[-1]
    return float((latest - mean) / std)


__all__ = ["fetch_netflow_history", "compute_zscore", "DEFAULT_LOOKBACK_DAYS"]
