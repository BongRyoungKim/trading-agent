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

    def test_quantile_defaults_to_module_constant(self) -> None:
        df = _make_binance_df(300)
        default_call = compute_vpin_features(df)
        explicit_call = compute_vpin_features(df, quantile=0.9)
        pd.testing.assert_series_equal(default_call["vpin_threshold"], explicit_call["vpin_threshold"])

    def test_higher_quantile_raises_the_threshold(self) -> None:
        df = _make_binance_df(300, seed=7)
        low_q = compute_vpin_features(df, quantile=0.9)
        high_q = compute_vpin_features(df, quantile=0.97)
        valid = low_q["vpin_threshold"].notna() & high_q["vpin_threshold"].notna()
        assert (high_q.loc[valid, "vpin_threshold"] >= low_q.loc[valid, "vpin_threshold"]).all()


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

    def test_higher_quantile_can_flip_a_borderline_signal_to_false(self) -> None:
        # 완만한 쏠림 -- q=0.9에서는 신호가 뜨지만 q=1.0(= 과거 200봉 중
        # 최댓값을 '초과'해야만 통과, 현재값==최댓값이라 '초과'를 만족 못함)
        # 에서는 같은 데이터로도 신호가 꺼질 수 있어야 한다.
        n = MIN_BARS_REQUIRED + 10
        df = _make_binance_df(n, volume=1000.0, delta=0.0, seed=4)
        df.loc[df.index[-WINDOW:], "delta"] = 1200.0
        df.loc[df.index[-WINDOW:], "volume"] = 1300.0
        baseline = latest_signal(df, quantile=0.9)
        stricter = latest_signal(df, quantile=1.0)
        assert baseline is True
        assert stricter is False
