"""Unit tests for src/ml_signal/features.py (ML 신호 피처 계산, 순수 함수)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.ml_signal.features import FEATURE_COLS, MIN_BARS_REQUIRED, compute_features, latest_feature_row


def _make_ohlcv(n: int, start_price: float = 100.0, trend: float = 0.0, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = start_price + np.cumsum(trend + rng.normal(0, 1, n))
    closes = np.clip(closes, 1, None)
    highs = closes + rng.uniform(0.1, 1.0, n)
    lows = closes - rng.uniform(0.1, 1.0, n)
    volumes = rng.uniform(1000, 2000, n)
    timestamps = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({
        "timestamp": timestamps, "open": closes, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    })


class TestComputeFeatures:
    def test_adds_all_feature_columns(self) -> None:
        df = _make_ohlcv(100)
        result = compute_features(df)
        for col in FEATURE_COLS:
            assert col in result.columns

    def test_no_lookahead_early_rows_are_nan(self) -> None:
        # 롤링 윈도우가 아직 안 찬 초반 행들은 NaN이어야 함 (미래참조 없음)
        df = _make_ohlcv(100)
        result = compute_features(df)
        assert result["ret_20d"].iloc[:20].isna().all()
        assert result["dist_20d_high"].iloc[:19].isna().all()

    def test_later_rows_fully_populated(self) -> None:
        df = _make_ohlcv(100)
        result = compute_features(df)
        assert not result[FEATURE_COLS].iloc[-1].isna().any()

    def test_recomputing_on_truncated_history_does_not_change_past_values(self) -> None:
        # 미래 데이터를 추가해도 과거 시점의 피처 값은 그대로여야 함(룩어헤드 없음 재확인)
        df = _make_ohlcv(100)
        full = compute_features(df)
        truncated = compute_features(df.iloc[:60])
        pd.testing.assert_series_equal(
            full[FEATURE_COLS].iloc[59], truncated[FEATURE_COLS].iloc[59], check_names=False,
        )


class TestLatestFeatureRow:
    def test_none_when_insufficient_history(self) -> None:
        df = _make_ohlcv(MIN_BARS_REQUIRED - 5)
        assert latest_feature_row(df) is None

    def test_returns_row_when_enough_history(self) -> None:
        df = _make_ohlcv(MIN_BARS_REQUIRED + 10)
        row = latest_feature_row(df)
        assert row is not None
        for col in FEATURE_COLS:
            assert col in row.index
            assert not pd.isna(row[col])

    def test_rsi_high_for_mostly_uptrend_with_occasional_dip(self) -> None:
        # 대부분 상승 + 가끔 소폭 하락(순수 상승만은 loss=0->NaN이 되는
        # 학습 스크립트와 동일한 RSI 공식의 알려진 엣지케이스, 실거래에서는
        # 사실상 안 나오는 완전 단조증가만 아니면 문제없이 높은 RSI가 나옴)
        n = 80
        timestamps = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
        steps = np.full(n, 10.0)
        steps[::5] = -3.0  # 5봉마다 실제로 하락(직전 대비 감소) -> loss가 0이 되지 않게
        closes = 100 + np.cumsum(steps)
        df = pd.DataFrame({
            "timestamp": timestamps, "open": closes, "high": closes + 1,
            "low": closes - 1, "close": closes, "volume": np.full(n, 1000.0),
        })
        row = latest_feature_row(df)
        assert row is not None
        assert row["rsi14"] > 60

    def test_rsi_nan_edge_case_for_pure_monotonic_uptrend_is_skipped_safely(self) -> None:
        # 완전 단조증가(하락 0봉)는 이 RSI 공식에서 NaN이 되는 알려진
        # 엣지케이스 -- latest_feature_row가 크래시 대신 None을 반환해야 함
        n = 80
        timestamps = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
        closes = np.arange(1, n + 1, dtype=float) * 10
        df = pd.DataFrame({
            "timestamp": timestamps, "open": closes, "high": closes + 1,
            "low": closes - 1, "close": closes, "volume": np.full(n, 1000.0),
        })
        assert latest_feature_row(df) is None
