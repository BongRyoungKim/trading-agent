"""Unit tests for src/vpin_signal/signal.py (VPIN+델타 신호 계산, 순수 함수)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.vpin_signal.signal import MIN_BARS_REQUIRED, WINDOW, compute_vpin_features, latest_signal


def _make_binance_df(n: int, volume: float = 1000.0, delta: float = 0.0, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    volumes = np.full(n, volume) + rng.normal(0, 1, n)
    deltas = np.full(n, delta) + rng.normal(0, 1, n)
    return pd.DataFrame({"timestamp": timestamps, "volume": volumes, "delta": deltas})


class TestComputeVpinFeatures:
    def test_adds_expected_columns(self) -> None:
        df = _make_binance_df(300)
        result = compute_vpin_features(df)
        for col in ["vpin", "vpin_threshold", "net_delta_window"]:
            assert col in result.columns

    def test_no_lookahead_early_rows_are_nan(self) -> None:
        df = _make_binance_df(300)
        result = compute_vpin_features(df, window=40)
        assert result["vpin"].iloc[:39].isna().all()

    def test_recomputing_on_truncated_history_does_not_change_past_values(self) -> None:
        df = _make_binance_df(300)
        full = compute_vpin_features(df)
        truncated = compute_vpin_features(df.iloc[:250])
        assert full["vpin"].iloc[249] == pytest.approx(truncated["vpin"].iloc[249])


class TestLatestSignal:
    def test_none_when_insufficient_history(self) -> None:
        df = _make_binance_df(MIN_BARS_REQUIRED - 10)
        assert latest_signal(df) is None

    def test_true_when_imbalance_spikes_with_positive_delta(self) -> None:
        # 평소엔 잔잔한 거래량/델타, 마지막 WINDOW개 봉에서 매수 쏠림 급증
        n = MIN_BARS_REQUIRED + 10
        df = _make_binance_df(n, volume=1000.0, delta=0.0, seed=1)
        df.loc[df.index[-WINDOW:], "delta"] = 5000.0  # 강한 매수 우위
        df.loc[df.index[-WINDOW:], "volume"] = 5500.0
        assert latest_signal(df) is True

    def test_false_when_no_imbalance(self) -> None:
        n = MIN_BARS_REQUIRED + 10
        df = _make_binance_df(n, volume=1000.0, delta=0.0, seed=2)
        # delta가 0 근처에서 랜덤 노이즈만 있으면 vpin이 특별히 임계값을 못 넘음
        result = latest_signal(df)
        assert result is False

    def test_false_when_imbalance_spikes_but_delta_is_negative(self) -> None:
        # 불균형은 크지만 매도 우위(패닉 매도)면 매수 신호가 아님
        n = MIN_BARS_REQUIRED + 10
        df = _make_binance_df(n, volume=1000.0, delta=0.0, seed=3)
        df.loc[df.index[-WINDOW:], "delta"] = -5000.0
        df.loc[df.index[-WINDOW:], "volume"] = 5500.0
        assert latest_signal(df) is False
