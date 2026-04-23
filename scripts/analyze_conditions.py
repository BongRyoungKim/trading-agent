"""
SwingMomentumStrategy 조건별 충족률 분석
각 필터가 얼마나 자주 충족되는지 확인 → 파라미터 병목 식별

Usage: python scripts/analyze_conditions.py
"""
from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.indicators import adx as calc_adx, atr, ema, macd, rsi as calc_rsi


def fetch_ohlcv(symbol: str, timeframe: str = "15m", days: int = 90) -> pd.DataFrame:
    exchange = ccxt.upbit({"enableRateLimit": True})
    tf_ms = {"15m": 900_000, "1h": 3_600_000}[timeframe]
    since = int((datetime.now(UTC) - timedelta(days=days)).timestamp() * 1000)

    all_rows: list[list] = []
    fetch_since = since
    while True:
        try:
            rows = exchange.fetch_ohlcv(symbol, timeframe, since=fetch_since, limit=200)
        except Exception as exc:
            print(f"API error: {exc}, retrying...")
            time.sleep(2)
            continue
        if not rows:
            break
        new = [r for r in rows if r[0] > (all_rows[-1][0] if all_rows else 0)]
        all_rows.extend(new)
        if rows[-1][0] >= int(datetime.now(UTC).timestamp() * 1000) - tf_ms:
            break
        if len(rows) < 200:
            break
        fetch_since = rows[-1][0] + 1
        time.sleep(0.3)

    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.sort_values("timestamp").reset_index(drop=True).astype(
        {"open": float, "high": float, "low": float, "close": float, "volume": float}
    )


def analyze(df: pd.DataFrame, symbol: str) -> None:
    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]
    n = len(df)

    print(f"\n{'='*60}")
    print(f"  {symbol} | 15m | {n}봉 | {str(df['timestamp'].iloc[0])[:10]} ~ {str(df['timestamp'].iloc[-1])[:10]}")
    print(f"{'='*60}")

    # 지표 계산 (벡터화 — 1회만)
    for ema_fast, ema_slow in [(10, 30), (15, 40), (20, 50)]:
        ema_f = ema(close, ema_fast)
        ema_s = ema(close, ema_slow)
        uptrend = (ema_f > ema_s)
        pct = uptrend.mean() * 100
        # 골든크로스 횟수
        crosses = ((ema_f > ema_s) & (ema_f.shift(1) <= ema_s.shift(1))).sum()
        print(f"  EMA{ema_fast}/{ema_slow} 상승추세: {pct:.1f}%  |  골든크로스: {crosses}회")

    print()

    # ADX
    adx_series = calc_adx(high, low, close)
    for thresh in [18, 22, 25, 28]:
        pct = (adx_series >= thresh).mean() * 100
        print(f"  ADX >= {thresh}: {pct:.1f}%")

    print()

    # Volume ratio
    vol_avg = vol.rolling(20).mean()
    vol_ratio = vol / vol_avg
    for mult in [1.2, 1.5, 2.0, 2.5]:
        pct = (vol_ratio >= mult).mean() * 100
        print(f"  Vol >= {mult}×: {pct:.1f}%")

    print()

    # RSI
    rsi_series = calc_rsi(close, 14)
    for thresh in [30, 35, 40, 45]:
        pct = (rsi_series < thresh).mean() * 100
        print(f"  RSI < {thresh} (과매도): {pct:.1f}%")

    print()

    # EMA proximity
    ema20 = ema(close, 20)
    prox = (close - ema20).abs() / ema20 * 100
    for pct_thresh in [2.0, 2.5, 3.5, 5.0]:
        pct = (prox <= pct_thresh).mean() * 100
        print(f"  EMA20 근접 <= {pct_thresh}%: {pct:.1f}%")

    print()

    # MACD histogram positive+rising
    macd_df = macd(close, 12, 26, 9)
    hist = macd_df["histogram"]
    macd_ok = (hist > 0) & (hist > hist.shift(1))
    print(f"  MACD hist 양수+상승: {macd_ok.mean()*100:.1f}%")

    # ── 조합 분석 (핵심 조합만) ──────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("  [ 조건 AND 조합 충족률 — 예상 진입 빈도 ]")
    print(f"{'─'*60}")

    combos = [
        dict(ema_f=10, ema_s=30, adx=18, vol=1.2, prox=6.0, rsi_os=40),
        dict(ema_f=10, ema_s=30, adx=20, vol=1.5, prox=4.0, rsi_os=40),
        dict(ema_f=15, ema_s=40, adx=22, vol=1.5, prox=4.0, rsi_os=40),
        dict(ema_f=15, ema_s=40, adx=25, vol=1.5, prox=3.5, rsi_os=40),
        dict(ema_f=20, ema_s=50, adx=22, vol=1.5, prox=4.0, rsi_os=40),
        dict(ema_f=20, ema_s=50, adx=28, vol=2.0, prox=2.5, rsi_os=40),  # 현재 설정
    ]

    rsi14 = calc_rsi(close, 14)
    adx_s = calc_adx(high, low, close)

    for c in combos:
        ef = ema(close, c["ema_f"])
        es = ema(close, c["ema_s"])
        va = vol.rolling(20).mean()
        prox_s = (close - ef).abs() / ef * 100

        cond = (
            (ef > es) &
            (adx_s >= c["adx"]) &
            (vol / va >= c["vol"]) &
            (prox_s <= c["prox"]) &
            macd_ok
        )
        # RSI lookback: 최근 4봉 안에 과매도
        rsi_os_window = rsi14.rolling(4).min() < c["rsi_os"]
        rsi_range = (rsi14 >= c["rsi_os"]) & (rsi14 < 65)
        full_cond = cond & rsi_os_window & rsi_range

        count = full_cond.sum()
        days_est = count * 15 / 60 / 24  # 15분봉 → 일
        label = "← 현재" if c == combos[-1] else ""
        print(
            f"  EMA{c['ema_f']}/{c['ema_s']} ADX{c['adx']} "
            f"Vol{c['vol']}× Prox{c['prox']}%: "
            f"{count}봉 ({count/n*100:.1f}%) ≈ {count}신호/90일 {label}"
        )


def main() -> None:
    symbols = ["BTC/KRW", "ETH/KRW", "XRP/KRW"]
    for sym in symbols:
        print(f"\n{sym} 데이터 수집 중...")
        df = fetch_ohlcv(sym, days=90)
        analyze(df, sym)


if __name__ == "__main__":
    main()
