"""
Technical indicator calculations using pure pandas (no ta-lib C extension).
All functions are pure: receive DataFrame, return Series or DataFrame.
Input DataFrame must have columns: open, high, low, close, volume (float64).
"""
from __future__ import annotations

import pandas as pd


def sma(close: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return close.rolling(window=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return close.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index (0–100).
    Uses Wilder's smoothing (equivalent to EMA with alpha=1/period).
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, float("inf"))
    return 100 - (100 / (1 + rs))


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """
    MACD indicator.

    Returns DataFrame with columns:
        macd       — MACD line (fast EMA − slow EMA)
        signal     — Signal line (EMA of MACD)
        histogram  — MACD − Signal
    """
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "signal": signal_line, "histogram": histogram}
    )


def bollinger_bands(
    close: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> pd.DataFrame:
    """
    Bollinger Bands.

    Returns DataFrame with columns:
        upper  — Upper band
        middle — Middle band (SMA)
        lower  — Lower band
        width  — Band width relative to middle (upper−lower)/middle
    """
    middle = sma(close, period)
    std = close.rolling(window=period).std(ddof=0)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    width = (upper - lower) / middle
    return pd.DataFrame(
        {"upper": upper, "middle": middle, "lower": lower, "width": width}
    )


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    Average True Range — measures volatility.
    Uses Wilder's smoothing (EMA with alpha=1/period).
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    Average Directional Index — measures trend strength (not direction).
    Values ≥ 25 indicate a trending market; < 25 indicates ranging/choppy.
    Uses Wilder's smoothing (EMA with alpha=1/period).
    """
    alpha = 1.0 / period
    h = high.reset_index(drop=True)
    l = low.reset_index(drop=True)
    c = close.reset_index(drop=True)

    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)

    up   = h.diff()
    down = -l.diff()
    pdm  = up.where((up > down) & (up > 0), 0.0)
    ndm  = down.where((down > up) & (down > 0), 0.0)

    tr_s  = tr.ewm(alpha=alpha, adjust=False).mean()
    pdm_s = pdm.ewm(alpha=alpha, adjust=False).mean()
    ndm_s = ndm.ewm(alpha=alpha, adjust=False).mean()

    pdi = 100.0 * pdm_s / tr_s.replace(0, float("nan"))
    ndi = 100.0 * ndm_s / tr_s.replace(0, float("nan"))

    dx = 100.0 * (pdi - ndi).abs() / (pdi + ndi).replace(0, float("nan"))
    return dx.ewm(alpha=alpha, adjust=False).mean().fillna(0.0)


def vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
) -> pd.Series:
    """
    Volume-Weighted Average Price (cumulative, resets each day if DatetimeIndex).
    """
    typical_price = (high + low + close) / 3
    cumulative_tp_vol = (typical_price * volume).cumsum()
    cumulative_vol = volume.cumsum()
    return cumulative_tp_vol / cumulative_vol


def add_indicators(
    df: pd.DataFrame,
    sma_periods: list[int] | None = None,
    ema_periods: list[int] | None = None,
    include_rsi: bool = True,
    include_macd: bool = True,
    include_bb: bool = True,
    include_atr: bool = True,
) -> pd.DataFrame:
    """
    Convenience function: add common indicators to an OHLCV DataFrame.

    Returns a new DataFrame (does not mutate input).
    Input columns required: open, high, low, close, volume.
    """
    result = df.copy()
    close = result["close"]
    high = result["high"]
    low = result["low"]

    for period in sma_periods or [20, 50, 200]:
        result[f"sma_{period}"] = sma(close, period)

    for period in ema_periods or [9, 21]:
        result[f"ema_{period}"] = ema(close, period)

    if include_rsi:
        result["rsi_14"] = rsi(close)

    if include_macd:
        macd_df = macd(close)
        result["macd"] = macd_df["macd"]
        result["macd_signal"] = macd_df["signal"]
        result["macd_hist"] = macd_df["histogram"]

    if include_bb:
        bb = bollinger_bands(close)
        result["bb_upper"] = bb["upper"]
        result["bb_middle"] = bb["middle"]
        result["bb_lower"] = bb["lower"]

    if include_atr:
        result["atr_14"] = atr(high, low, close)

    return result
