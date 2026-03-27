"""Unit tests for StrategyOptimizer."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from src.backtest.optimizer import OptimizationResult, StrategyOptimizer, _RANK_METRICS
from src.backtest.runner import BacktestConfig
from src.strategy.ma_crossover import MACrossoverStrategy


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_oscillating_ohlcv(n: int = 600) -> pd.DataFrame:
    """Sine-wave prices that create multiple MA crossovers."""
    import numpy as np
    t = np.linspace(0, 6 * np.pi, n)
    prices = 50000.0 + 10000.0 * np.sin(t)
    base = datetime(2023, 1, 1, tzinfo=UTC)
    rows = [{"open": p, "high": p * 1.003, "low": p * 0.997, "close": p, "volume": 1000.0}
            for p in prices]
    index = pd.DatetimeIndex([base + timedelta(hours=i) for i in range(n)])
    return pd.DataFrame(rows, index=index)


def _make_flat_ohlcv(n: int = 200) -> pd.DataFrame:
    base = datetime(2023, 1, 1, tzinfo=UTC)
    rows = [{"open": 50000.0, "high": 50100.0, "low": 49900.0, "close": 50000.0, "volume": 100.0}
            for _ in range(n)]
    index = pd.DatetimeIndex([base + timedelta(hours=i) for i in range(n)])
    return pd.DataFrame(rows, index=index)


# ── Init validation ───────────────────────────────────────────────────────────

class TestStrategyOptimizerInit:
    def test_invalid_rank_by_raises(self):
        with pytest.raises(ValueError, match="rank_by"):
            StrategyOptimizer(
                strategy_class=MACrossoverStrategy,
                fixed_params={"symbol": "BTC/USDT"},
                param_grid={"fast_period": [5, 10]},
                rank_by="nonexistent_metric",
            )

    def test_empty_param_grid_raises(self):
        with pytest.raises(ValueError, match="param_grid"):
            StrategyOptimizer(
                strategy_class=MACrossoverStrategy,
                fixed_params={"symbol": "BTC/USDT"},
                param_grid={},
            )

    def test_valid_init(self):
        opt = StrategyOptimizer(
            strategy_class=MACrossoverStrategy,
            fixed_params={"symbol": "BTC/USDT"},
            param_grid={"fast_period": [5, 10], "slow_period": [20, 50]},
            rank_by="sharpe_ratio",
        )
        assert opt._rank_by == "sharpe_ratio"


# ── Combination generation ────────────────────────────────────────────────────

class TestCombinationGeneration:
    def _opt(self, grid):
        return StrategyOptimizer(
            strategy_class=MACrossoverStrategy,
            fixed_params={"symbol": "BTC/USDT"},
            param_grid=grid,
        )

    def test_single_param_single_value(self):
        combos = list(self._opt({"fast_period": [5]})._iter_combinations())
        assert len(combos) == 1
        assert combos[0]["fast_period"] == 5

    def test_cartesian_product(self):
        combos = list(self._opt({
            "fast_period": [5, 10],
            "slow_period": [20, 50],
        })._iter_combinations())
        assert len(combos) == 4  # 2 × 2

    def test_fixed_params_included_in_each_combination(self):
        opt = StrategyOptimizer(
            strategy_class=MACrossoverStrategy,
            fixed_params={"symbol": "BTC/USDT", "ma_type": "sma"},
            param_grid={"fast_period": [5, 10]},
        )
        for combo in opt._iter_combinations():
            assert combo["symbol"] == "BTC/USDT"
            assert combo["ma_type"] == "sma"

    def test_three_param_grid(self):
        combos = list(self._opt({
            "fast_period": [5, 10, 20],
            "slow_period": [30, 50],
            "ma_type": ["ema", "sma"],
        })._iter_combinations())
        assert len(combos) == 12  # 3 × 2 × 2


# ── Single run ────────────────────────────────────────────────────────────────

class TestRunSingle:
    def test_invalid_params_returns_none(self):
        opt = StrategyOptimizer(
            strategy_class=MACrossoverStrategy,
            fixed_params={"symbol": "BTC/USDT"},
            param_grid={"fast_period": [5]},
            min_trades=1,
        )
        # fast >= slow is invalid for MACrossoverStrategy
        result = opt._run_single(_make_oscillating_ohlcv(), {"symbol": "BTC/USDT", "fast_period": 50, "slow_period": 20})
        assert result is None

    def test_insufficient_trades_returns_none(self):
        opt = StrategyOptimizer(
            strategy_class=MACrossoverStrategy,
            fixed_params={"symbol": "BTC/USDT"},
            param_grid={"fast_period": [5]},
            min_trades=999,  # impossibly high
        )
        result = opt._run_single(
            _make_oscillating_ohlcv(),
            {"symbol": "BTC/USDT", "fast_period": 5, "slow_period": 20},
        )
        assert result is None

    def test_valid_params_returns_result(self):
        opt = StrategyOptimizer(
            strategy_class=MACrossoverStrategy,
            fixed_params={"symbol": "BTC/USDT"},
            param_grid={"fast_period": [5]},
            min_trades=1,
        )
        result = opt._run_single(
            _make_oscillating_ohlcv(600),
            {"symbol": "BTC/USDT", "fast_period": 5, "slow_period": 20},
        )
        assert result is not None
        assert isinstance(result, OptimizationResult)


# ── Full optimization run ─────────────────────────────────────────────────────

class TestStrategyOptimizerRun:
    def test_run_returns_sorted_list(self):
        data = _make_oscillating_ohlcv(800)
        opt = StrategyOptimizer(
            strategy_class=MACrossoverStrategy,
            fixed_params={"symbol": "BTC/USDT"},
            param_grid={
                "fast_period": [5, 10],
                "slow_period": [20, 50],
            },
            min_trades=1,
            rank_by="total_return_pct",
        )
        results = opt.run(data)
        assert isinstance(results, list)
        # Results should be sorted descending by rank_value
        for i in range(1, len(results)):
            assert results[i - 1].rank_value >= results[i].rank_value

    def test_run_returns_empty_on_no_valid_combinations(self):
        data = _make_flat_ohlcv(50)  # flat, no crossovers
        opt = StrategyOptimizer(
            strategy_class=MACrossoverStrategy,
            fixed_params={"symbol": "BTC/USDT"},
            param_grid={"fast_period": [5], "slow_period": [20]},
            min_trades=10,  # flat data will produce 0 trades
        )
        results = opt.run(data)
        assert results == []

    def test_best_params_returns_dict(self):
        data = _make_oscillating_ohlcv(800)
        opt = StrategyOptimizer(
            strategy_class=MACrossoverStrategy,
            fixed_params={"symbol": "BTC/USDT"},
            param_grid={
                "fast_period": [5, 10],
                "slow_period": [30, 60],
            },
            min_trades=1,
        )
        best = opt.best_params(data)
        assert best is not None
        assert "fast_period" in best
        assert "slow_period" in best

    def test_best_params_none_when_no_results(self):
        data = _make_flat_ohlcv(50)
        opt = StrategyOptimizer(
            strategy_class=MACrossoverStrategy,
            fixed_params={"symbol": "BTC/USDT"},
            param_grid={"fast_period": [5], "slow_period": [20]},
            min_trades=999,
        )
        assert opt.best_params(data) is None

    def test_rank_by_max_drawdown_negated_correctly(self):
        """max_drawdown_pct (lower is better) should rank lower drawdown first."""
        data = _make_oscillating_ohlcv(800)
        opt = StrategyOptimizer(
            strategy_class=MACrossoverStrategy,
            fixed_params={"symbol": "BTC/USDT"},
            param_grid={
                "fast_period": [5, 10],
                "slow_period": [30, 60],
            },
            rank_by="max_drawdown_pct",
            min_trades=1,
        )
        results = opt.run(data)
        if len(results) >= 2:
            # rank_values are negated drawdowns; higher (less negative) = lower actual drawdown
            assert results[0].rank_value >= results[1].rank_value
            # The actual drawdown of the best result should be ≤ the second best
            assert results[0].result.max_drawdown_pct <= results[1].result.max_drawdown_pct

    def test_optimization_result_str(self):
        data = _make_oscillating_ohlcv(800)
        opt = StrategyOptimizer(
            strategy_class=MACrossoverStrategy,
            fixed_params={"symbol": "BTC/USDT"},
            param_grid={"fast_period": [5], "slow_period": [20]},
            min_trades=1,
        )
        results = opt.run(data)
        if results:
            s = str(results[0])
            assert "sharpe_ratio" in s
            assert "return=" in s

    @pytest.mark.parametrize("metric", list(_RANK_METRICS))
    def test_all_rank_metrics_accepted(self, metric):
        opt = StrategyOptimizer(
            strategy_class=MACrossoverStrategy,
            fixed_params={"symbol": "BTC/USDT"},
            param_grid={"fast_period": [5]},
            rank_by=metric,
        )
        assert opt._rank_by == metric
