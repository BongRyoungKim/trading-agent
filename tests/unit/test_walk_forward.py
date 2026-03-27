"""Unit tests for WalkForwardAnalyzer."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from src.backtest.runner import BacktestConfig
from src.backtest.walk_forward import WalkForwardAnalyzer, WalkForwardReport


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ohlcv(n_bars: int, price: float = 50000.0) -> pd.DataFrame:
    """Build a flat OHLCV DataFrame."""
    base = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    for i in range(n_bars):
        p = price
        rows.append({
            "open": p,
            "high": p * 1.005,
            "low": p * 0.995,
            "close": p,
            "volume": 100.0,
        })
    index = pd.DatetimeIndex([base + timedelta(hours=i) for i in range(n_bars)])
    return pd.DataFrame(rows, index=index)


def _make_trending_ohlcv(n_bars: int, start: float = 40000.0, end: float = 60000.0) -> pd.DataFrame:
    """Build a trending (rising) OHLCV DataFrame."""
    import numpy as np
    base = datetime(2024, 1, 1, tzinfo=UTC)
    prices = np.linspace(start, end, n_bars)
    rows = [{"open": p, "high": p * 1.005, "low": p * 0.995, "close": p, "volume": 100.0}
            for p in prices]
    index = pd.DatetimeIndex([base + timedelta(hours=i) for i in range(n_bars)])
    return pd.DataFrame(rows, index=index)


def _mock_strategy(fast: int = 5, slow: int = 10) -> object:
    from src.strategy.ma_crossover import MACrossoverStrategy
    return MACrossoverStrategy(symbol="BTC/USDT", fast_period=fast, slow_period=slow)


# ── Init validation ───────────────────────────────────────────────────────────

class TestWalkForwardAnalyzerInit:
    def test_raises_if_is_bars_not_positive(self):
        with pytest.raises(ValueError, match="is_bars"):
            WalkForwardAnalyzer(_mock_strategy(), is_bars=0, oos_bars=20)

    def test_raises_if_oos_bars_not_positive(self):
        with pytest.raises(ValueError, match="oos_bars"):
            WalkForwardAnalyzer(_mock_strategy(), is_bars=100, oos_bars=0)

    def test_raises_if_is_bars_not_greater_than_oos(self):
        with pytest.raises(ValueError, match="is_bars must be greater"):
            WalkForwardAnalyzer(_mock_strategy(), is_bars=50, oos_bars=50)

    def test_valid_init(self):
        wfa = WalkForwardAnalyzer(_mock_strategy(), is_bars=100, oos_bars=30)
        assert wfa._is_bars == 100
        assert wfa._oos_bars == 30


# ── Window generation ─────────────────────────────────────────────────────────

class TestWindowGeneration:
    def _collect_windows(self, data, is_bars, oos_bars, anchored=False):
        wfa = WalkForwardAnalyzer(_mock_strategy(), is_bars=is_bars, oos_bars=oos_bars,
                                  anchored=anchored)
        return list(wfa._windows(data))

    def test_rolling_window_count(self):
        # 300 bars, IS=100, OOS=50 → folds at [0:100/100:150], [50:150/150:200], [100:200/200:250], [150:250/250:300]
        data = _make_ohlcv(300)
        windows = self._collect_windows(data, is_bars=100, oos_bars=50)
        assert len(windows) == 4

    def test_rolling_is_window_size(self):
        data = _make_ohlcv(300)
        windows = self._collect_windows(data, is_bars=100, oos_bars=50)
        for is_df, oos_df in windows:
            assert len(is_df) == 100

    def test_rolling_oos_window_size(self):
        data = _make_ohlcv(300)
        windows = self._collect_windows(data, is_bars=100, oos_bars=50)
        for is_df, oos_df in windows:
            assert len(oos_df) == 50

    def test_rolling_no_overlap_between_is_and_oos(self):
        data = _make_ohlcv(300)
        windows = self._collect_windows(data, is_bars=100, oos_bars=50)
        for is_df, oos_df in windows:
            assert is_df.index[-1] < oos_df.index[0]

    def test_anchored_is_grows_each_fold(self):
        data = _make_ohlcv(500)
        windows = self._collect_windows(data, is_bars=100, oos_bars=50, anchored=True)
        is_sizes = [len(w[0]) for w in windows]
        # Each fold's IS should be larger than the previous (expanding)
        for i in range(1, len(is_sizes)):
            assert is_sizes[i] > is_sizes[i - 1]

    def test_anchored_oos_constant_size(self):
        data = _make_ohlcv(500)
        windows = self._collect_windows(data, is_bars=100, oos_bars=50, anchored=True)
        for is_df, oos_df in windows:
            assert len(oos_df) == 50

    def test_insufficient_data_returns_no_windows(self):
        # Only 100 bars for is_bars=100, oos_bars=50 → need 150 minimum
        data = _make_ohlcv(100)
        windows = self._collect_windows(data, is_bars=100, oos_bars=50)
        assert len(windows) == 0

    def test_exact_minimum_data_yields_one_window(self):
        # Exactly 150 bars for is_bars=100, oos_bars=50
        data = _make_ohlcv(150)
        windows = self._collect_windows(data, is_bars=100, oos_bars=50)
        assert len(windows) == 1


# ── WalkForwardReport ─────────────────────────────────────────────────────────

class TestWalkForwardReport:
    def _make_fold(self, is_ret: float, oos_ret: float, idx: int = 0):
        from src.backtest.models import BacktestResult
        base = datetime(2024, 1, 1, tzinfo=UTC)

        def _result(ret):
            initial = Decimal("10000")
            final = initial * Decimal(str(1 + ret / 100))
            return BacktestResult(
                strategy_name="test", symbol="X", timeframe="1h",
                start_date=base, end_date=base + timedelta(days=30),
                initial_capital=initial, final_capital=final, trades=(),
                total_return_pct=ret,
            )

        from src.backtest.walk_forward import WalkForwardFold
        return WalkForwardFold(
            fold_index=idx,
            is_start=pd.Timestamp(base),
            is_end=pd.Timestamp(base + timedelta(days=10)),
            oos_start=pd.Timestamp(base + timedelta(days=10)),
            oos_end=pd.Timestamp(base + timedelta(days=20)),
            is_result=_result(is_ret),
            oos_result=_result(oos_ret),
        )

    def test_empty_report(self):
        report = WalkForwardReport()
        assert report.n_folds == 0
        assert report.avg_oos_return_pct == 0.0
        assert report.oos_win_rate_pct == 0.0
        assert report.efficiency_ratio == 0.0

    def test_avg_oos_return(self):
        report = WalkForwardReport(folds=[
            self._make_fold(10, 5, 0),
            self._make_fold(8, -2, 1),
            self._make_fold(12, 7, 2),
        ])
        # avg OOS = (5 - 2 + 7) / 3 = 10/3
        assert abs(report.avg_oos_return_pct - 10 / 3) < 1e-9

    def test_oos_win_rate(self):
        report = WalkForwardReport(folds=[
            self._make_fold(10, 5, 0),
            self._make_fold(8, -2, 1),
            self._make_fold(12, 3, 2),
        ])
        # 2 out of 3 OOS profitable → 66.67%
        assert abs(report.oos_win_rate_pct - 200 / 3) < 1e-9

    def test_efficiency_ratio(self):
        report = WalkForwardReport(folds=[
            self._make_fold(10, 5, 0),
            self._make_fold(10, 5, 1),
        ])
        # avg IS = 10, avg OOS = 5 → ratio = 0.5
        assert abs(report.efficiency_ratio - 0.5) < 1e-9

    def test_summary_contains_fold_count(self):
        report = WalkForwardReport(folds=[self._make_fold(5, 3)])
        summary = report.summary()
        assert "1 folds" in summary
        assert "Fold 00" in summary


# ── Full run ──────────────────────────────────────────────────────────────────

class TestWalkForwardAnalyzerRun:
    def test_run_returns_report(self):
        data = _make_ohlcv(400)
        wfa = WalkForwardAnalyzer(_mock_strategy(), is_bars=100, oos_bars=50)
        report = wfa.run(data)
        assert isinstance(report, WalkForwardReport)

    def test_run_produces_expected_fold_count(self):
        # 400 bars, IS=100, OOS=50, rolling → folds: [0:100/100:150], [50:150/150:200], ...
        # total = (400 - 100) / 50 = 6 folds
        data = _make_ohlcv(400)
        wfa = WalkForwardAnalyzer(_mock_strategy(), is_bars=100, oos_bars=50)
        report = wfa.run(data)
        assert report.n_folds == 6

    def test_run_anchored_mode(self):
        data = _make_trending_ohlcv(400)
        wfa = WalkForwardAnalyzer(
            _mock_strategy(fast=5, slow=10),
            is_bars=60,
            oos_bars=30,
            anchored=True,
        )
        report = wfa.run(data)
        # anchored: (400 - 60) / 30 = 11.33 → 11 folds
        assert report.n_folds == 11

    def test_insufficient_data_returns_empty_report(self):
        data = _make_ohlcv(50)  # less than is_bars + oos_bars
        wfa = WalkForwardAnalyzer(_mock_strategy(), is_bars=100, oos_bars=50)
        report = wfa.run(data)
        assert report.n_folds == 0
