"""
Local OHLCV cache for backtest research.

Fetch a symbol/timeframe/date-range once from the exchange and reuse it
across research rounds instead of re-downloading every time (the previous
few research rounds each re-fetched the same 11 symbols from Upbit into
scratch directories — this module replaces that pattern with one shared,
version-controlled-but-gitignored cache under data/backtest_cache/).

See docs/research-protocol.md for how cached data feeds the
train/validation/test split used by strategy research.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backtest_cache"


def cache_path(symbol: str, timeframe: str) -> Path:
    """e.g. cache_path("BTC/KRW", "15m") -> data/backtest_cache/BTC_KRW_15m.json"""
    safe_symbol = symbol.replace("/", "_")
    return CACHE_DIR / f"{safe_symbol}_{timeframe}.json"


def is_cached(symbol: str, timeframe: str = "15m") -> bool:
    return cache_path(symbol, timeframe).exists()


def load_cached_ohlcv(symbol: str, timeframe: str = "15m") -> pd.DataFrame:
    """
    Load previously-cached OHLCV into a DataFrame with columns
    open/high/low/close/volume/timestamp (oldest row first) — the exact
    contract BaseStrategy.generate_signal() and the exit-backtest engine
    expect.

    Raises:
        FileNotFoundError: if fetch_and_cache_ohlcv() hasn't been run yet
            for this symbol/timeframe.
    """
    path = cache_path(symbol, timeframe)
    if not path.exists():
        raise FileNotFoundError(
            f"No cached OHLCV for {symbol} {timeframe} at {path}. "
            f"Call fetch_and_cache_ohlcv({symbol!r}, ...) first."
        )
    with open(path) as f:
        rows = json.load(f)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop(columns=["ts"]).sort_values("timestamp").reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df


def fetch_and_cache_ohlcv(
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe: str = "15m",
    exchange_id: str = "upbit",
    force: bool = False,
) -> pd.DataFrame:
    """
    Fetch OHLCV candles for [start, end] from `exchange_id` via ccxt and
    write them to the local cache, then return them via load_cached_ohlcv().
    If already cached and `force=False`, skips the network call entirely.

    Paginates backward using ccxt's `to`-param convention (matches Upbit's
    candle API; the only exchange this project currently backtests against).
    """
    if not force and is_cached(symbol, timeframe):
        return load_cached_ohlcv(symbol, timeframe)

    import time as _time

    import ccxt

    exchange = getattr(ccxt, exchange_id)()
    exchange.enableRateLimit = True

    all_rows: list[list] = []
    cursor = end
    seen_min_ts: int | None = None
    while True:
        to_str = cursor.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=200, params={"to": to_str})
        except Exception:  # noqa: BLE001 — rate limit / transient network errors
            _time.sleep(2)
            continue
        if not batch:
            break
        all_rows.extend(batch)
        min_ts = batch[0][0]
        if seen_min_ts is not None and min_ts >= seen_min_ts:
            break
        seen_min_ts = min_ts
        cursor = datetime.fromtimestamp(min_ts / 1000, tz=timezone.utc)
        if cursor <= start:
            break
        _time.sleep(0.05)

    uniq = {r[0]: r for r in all_rows}
    rows = sorted(uniq.values(), key=lambda r: r[0])
    rows = [r for r in rows if start.timestamp() * 1000 <= r[0] <= end.timestamp() * 1000]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_path(symbol, timeframe), "w") as f:
        json.dump(rows, f)

    return load_cached_ohlcv(symbol, timeframe)
