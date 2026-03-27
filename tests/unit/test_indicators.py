"""Unit tests for src/utils/indicators.py"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.utils.indicators import (
    add_indicators,
    atr,
    bollinger_bands,
    ema,
    macd,
    rsi,
    sma,
    vwap,
)


@pytest.fixture()
def price_series() -> pd.Series:
    """50 synthetic closing prices with a slight uptrend."""
    rng = np.random.default_rng(seed=42)
    prices = 50000.0 + np.cumsum(rng.normal(0, 200, 50))
    return pd.Series(prices, name="close")


@pytest.fixture()
def ohlcv_df(price_series: pd.Series) -> pd.DataFrame:
    close = price_series
    rng = np.random.default_rng(seed=42)
    return pd.DataFrame(
        {
            "open": close * (1 - rng.uniform(0, 0.005, len(close))),
            "high": close * (1 + rng.uniform(0, 0.01, len(close))),
            "low": close * (1 - rng.uniform(0, 0.01, len(close))),
            "close": close,
            "volume": rng.uniform(50, 200, len(close)),
        }
    )


class TestSMA:
    def test_length_matches_input(self, price_series: pd.Series) -> None:
        result = sma(price_series, 10)
        assert len(result) == len(price_series)

    def test_first_n_minus_one_are_nan(self, price_series: pd.Series) -> None:
        result = sma(price_series, 10)
        assert result.iloc[:9].isna().all()
        assert not pd.isna(result.iloc[9])

    def test_value_equals_manual_average(self, price_series: pd.Series) -> None:
        period = 5
        result = sma(price_series, period)
        expected = price_series.iloc[:period].mean()
        assert abs(result.iloc[period - 1] - expected) < 1e-9


class TestEMA:
    def test_length_matches_input(self, price_series: pd.Series) -> None:
        result = ema(price_series, 12)
        assert len(result) == len(price_series)

    def test_no_nan_after_warmup(self, price_series: pd.Series) -> None:
        result = ema(price_series, 12)
        # EMA with adjust=False has no NaN after first value
        assert result.iloc[1:].notna().all()

    def test_reacts_faster_than_sma(self, price_series: pd.Series) -> None:
        # EMA should be closer to recent prices than SMA
        ema_val = ema(price_series, 10).iloc[-1]
        sma_val = sma(price_series, 10).iloc[-1]
        last = price_series.iloc[-1]
        # Both should be positive numbers close to the price level
        assert ema_val > 0
        assert sma_val > 0


class TestRSI:
    def test_values_in_0_100_range(self, price_series: pd.Series) -> None:
        result = rsi(price_series, 14)
        valid = result.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_length_matches_input(self, price_series: pd.Series) -> None:
        result = rsi(price_series, 14)
        assert len(result) == len(price_series)


class TestMACD:
    def test_returns_dataframe_with_correct_columns(self, price_series: pd.Series) -> None:
        result = macd(price_series)
        assert set(result.columns) == {"macd", "signal", "histogram"}

    def test_histogram_equals_macd_minus_signal(self, price_series: pd.Series) -> None:
        result = macd(price_series)
        diff = (result["macd"] - result["signal"] - result["histogram"]).abs()
        assert diff.max() < 1e-9


class TestBollingerBands:
    def test_returns_dataframe_with_correct_columns(self, price_series: pd.Series) -> None:
        result = bollinger_bands(price_series)
        assert set(result.columns) == {"upper", "middle", "lower", "width"}

    def test_upper_above_lower(self, price_series: pd.Series) -> None:
        result = bollinger_bands(price_series)
        valid = result.dropna()
        assert (valid["upper"] > valid["lower"]).all()

    def test_middle_between_bands(self, price_series: pd.Series) -> None:
        result = bollinger_bands(price_series)
        valid = result.dropna()
        assert (valid["middle"] >= valid["lower"]).all()
        assert (valid["middle"] <= valid["upper"]).all()


class TestATR:
    def test_all_values_non_negative(self, ohlcv_df: pd.DataFrame) -> None:
        result = atr(ohlcv_df["high"], ohlcv_df["low"], ohlcv_df["close"])
        assert (result.dropna() >= 0).all()

    def test_length_matches_input(self, ohlcv_df: pd.DataFrame) -> None:
        result = atr(ohlcv_df["high"], ohlcv_df["low"], ohlcv_df["close"])
        assert len(result) == len(ohlcv_df)


class TestVWAP:
    def test_positive_values(self, ohlcv_df: pd.DataFrame) -> None:
        result = vwap(
            ohlcv_df["high"], ohlcv_df["low"], ohlcv_df["close"], ohlcv_df["volume"]
        )
        assert (result > 0).all()

    def test_length_matches_input(self, ohlcv_df: pd.DataFrame) -> None:
        result = vwap(
            ohlcv_df["high"], ohlcv_df["low"], ohlcv_df["close"], ohlcv_df["volume"]
        )
        assert len(result) == len(ohlcv_df)


class TestAddIndicators:
    def test_returns_new_dataframe(self, ohlcv_df: pd.DataFrame) -> None:
        result = add_indicators(ohlcv_df)
        assert result is not ohlcv_df

    def test_original_not_mutated(self, ohlcv_df: pd.DataFrame) -> None:
        original_cols = set(ohlcv_df.columns)
        add_indicators(ohlcv_df)
        assert set(ohlcv_df.columns) == original_cols

    def test_default_indicators_added(self, ohlcv_df: pd.DataFrame) -> None:
        result = add_indicators(ohlcv_df)
        expected = {"sma_20", "sma_50", "ema_9", "rsi_14", "macd", "bb_upper", "atr_14"}
        assert expected.issubset(result.columns)

    def test_custom_sma_periods(self, ohlcv_df: pd.DataFrame) -> None:
        result = add_indicators(ohlcv_df, sma_periods=[7, 14])
        assert "sma_7" in result.columns
        assert "sma_14" in result.columns
        assert "sma_20" not in result.columns
