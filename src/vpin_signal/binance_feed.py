"""
바이낸스 테이커 매수/매도 거래량 조회 — VPIN 신호 계산용 원본 데이터.

Upbit/ccxt OHLCV에는 없는 `taker_buy_base_volume` 필드가 필요해 바이낸스
공개 REST API(인증 불필요)를 직접 호출한다. 네트워크 I/O를 signal.py의
순수 계산 로직과 분리(src/onchain/netflow.py와 동일 원칙).
"""
from __future__ import annotations

import json
import urllib.request

import pandas as pd

BASE_URL = "https://api.binance.com/api/v3/klines"


def klines_to_dataframe(rows: list[list]) -> pd.DataFrame:
    """바이낸스 raw klines 응답(list-of-lists)을 timestamp/volume/delta
    (taker_buy-taker_sell) 컬럼만 남긴 DataFrame으로 변환.

    2026-10-25 추가 — fetch_recent_klines()의 컬럼 조립 로직을 추출한
    것. scripts/vpin_backtest.py가 날짜범위 페이지네이션으로 받아온
    raw rows를 동일한 방식으로 파싱하기 위해 공유한다(파싱 로직 중복
    금지 — VPIN 공식이 실거래 경로와 어긋나면 안 됨)."""
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "qav", "n_trades", "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["volume"] = df["volume"].astype(float)
    df["taker_buy_base"] = df["taker_buy_base"].astype(float)
    df["taker_sell_base"] = df["volume"] - df["taker_buy_base"]
    df["delta"] = df["taker_buy_base"] - df["taker_sell_base"]
    return df[["timestamp", "volume", "delta"]]


def fetch_recent_klines(symbol: str, interval: str = "15m", limit: int = 260) -> pd.DataFrame:
    """최근 `limit`개 봉을 조회해 timestamp/volume/delta(taker_buy-taker_sell)
    컬럼만 남긴 DataFrame으로 반환. API 실패 시 예외를 그대로 전파(호출자가
    "이전 값 유지" 등 정책을 결정)."""
    url = f"{BASE_URL}?symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        rows = json.loads(resp.read().decode("utf-8"))
    return klines_to_dataframe(rows)


__all__ = ["fetch_recent_klines", "klines_to_dataframe"]
