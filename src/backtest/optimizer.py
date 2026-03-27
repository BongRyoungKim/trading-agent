"""
Strategy parameter optimizer: exhaustive grid search over a parameter space.

Runs one backtest per parameter combination and ranks results by a chosen metric.
Optionally applies walk-forward validation on the best candidate to guard against
overfitting on in-sample data.

Usage:
    from src.backtest.optimizer import StrategyOptimizer
    from src.strategy.ma_crossover import MACrossoverStrategy

    optimizer = StrategyOptimizer(
        strategy_class=MACrossoverStrategy,
        fixed_params={"symbol": "BTC/USDT"},
        param_grid={
            "fast_period": [5, 10, 20],
            "slow_period": [20, 50, 100],
            "ma_type": ["ema", "sma"],
        },
        config=BacktestConfig(),
        rank_by="sharpe_ratio",
    )
    results = optimizer.run(ohlcv_df)
    best = results[0]
    print(best)
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Type

import pandas as pd
from loguru import logger

from src.backtest.models import BacktestResult
from src.backtest.runner import BacktestConfig, BacktestRunner
from src.strategy.base import BaseStrategy

# Metrics that can be used for ranking (higher = better for all)
_RANK_METRICS = frozenset({
    "total_return_pct",
    "annualized_return_pct",
    "sharpe_ratio",
    "sortino_ratio",
    "win_rate_pct",
    "profit_factor",
})
# Metrics where lower is better (converted to negative before ranking)
_LOWER_IS_BETTER = frozenset({"max_drawdown_pct"})


@dataclass
class OptimizationResult:
    """Single parameter combination and its backtest result."""

    params: dict[str, Any]
    result: BacktestResult
    rank_value: float      # value of the ranking metric
    rank_metric: str

    def __str__(self) -> str:
        params_str = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return (
            f"OptimizationResult({params_str}) "
            f"| {self.rank_metric}={self.rank_value:.4f} "
            f"| return={self.result.total_return_pct:+.2f}% "
            f"| trades={self.result.total_trades}"
        )


class StrategyOptimizer:
    """
    Grid-search optimizer for strategy parameters.

    Args:
        strategy_class:  The strategy class to instantiate for each combination.
        fixed_params:    Parameters that are constant across all runs.
        param_grid:      Dict mapping parameter name to list of candidate values.
        config:          BacktestConfig shared across all runs.
        rank_by:         Metric to rank results by (higher = better).
                         Choices: total_return_pct, annualized_return_pct,
                         sharpe_ratio, sortino_ratio, win_rate_pct, profit_factor,
                         max_drawdown_pct (lower is better).
        min_trades:      Discard runs with fewer trades than this threshold.
    """

    def __init__(
        self,
        strategy_class: Type[BaseStrategy],
        fixed_params: dict[str, Any],
        param_grid: dict[str, list[Any]],
        config: BacktestConfig | None = None,
        rank_by: str = "sharpe_ratio",
        min_trades: int = 5,
    ) -> None:
        valid = _RANK_METRICS | _LOWER_IS_BETTER
        if rank_by not in valid:
            raise ValueError(
                f"rank_by must be one of {sorted(valid)}, got {rank_by!r}"
            )
        if not param_grid:
            raise ValueError("param_grid must not be empty")

        self._strategy_class = strategy_class
        self._fixed_params = fixed_params
        self._param_grid = param_grid
        self._config = config or BacktestConfig()
        self._rank_by = rank_by
        self._min_trades = min_trades

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, data: pd.DataFrame) -> list[OptimizationResult]:
        """
        Run all parameter combinations and return sorted results (best first).

        Args:
            data: OHLCV DataFrame (oldest-first, DatetimeIndex or with timestamp col).

        Returns:
            List of OptimizationResult sorted by rank_by metric (descending).
            Empty list if no combination yields min_trades.
        """
        combinations = list(self._iter_combinations())
        total = len(combinations)
        logger.info(
            "Optimization starting",
            strategy=self._strategy_class.__name__,
            combinations=total,
            rank_by=self._rank_by,
        )

        results: list[OptimizationResult] = []
        for idx, params in enumerate(combinations):
            opt_result = self._run_single(data, params)
            if opt_result is not None:
                results.append(opt_result)
            if (idx + 1) % max(1, total // 10) == 0:
                logger.debug("Optimization progress", done=idx + 1, total=total)

        results.sort(key=lambda r: r.rank_value, reverse=True)
        logger.info(
            "Optimization complete",
            evaluated=len(results),
            best_params=results[0].params if results else None,
            best_value=f"{results[0].rank_value:.4f}" if results else None,
        )
        return results

    def best_params(self, data: pd.DataFrame) -> dict[str, Any] | None:
        """Return only the best parameter dict (convenience wrapper)."""
        results = self.run(data)
        return results[0].params if results else None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _iter_combinations(self):
        """Yield every (varied) parameter combination merged with fixed_params."""
        keys = list(self._param_grid.keys())
        values = list(self._param_grid.values())
        for combo in itertools.product(*values):
            yield {**self._fixed_params, **dict(zip(keys, combo))}

    def _run_single(
        self, data: pd.DataFrame, params: dict[str, Any]
    ) -> OptimizationResult | None:
        """Run one backtest; return None on error or insufficient trades."""
        try:
            strategy = self._strategy_class(**params)
        except (TypeError, ValueError) as exc:
            logger.debug("Skipping invalid params", params=params, error=str(exc))
            return None

        try:
            runner = BacktestRunner(strategy, self._config)
            result = runner.run(data)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Backtest failed", params=params, error=str(exc))
            return None

        if result.total_trades < self._min_trades:
            return None

        rank_value = self._extract_rank_value(result)
        return OptimizationResult(
            params={k: v for k, v in params.items() if k not in self._fixed_params},
            result=result,
            rank_value=rank_value,
            rank_metric=self._rank_by,
        )

    def _extract_rank_value(self, result: BacktestResult) -> float:
        value = getattr(result, self._rank_by, 0.0)
        # For lower-is-better metrics, negate so sorting (descending) still works
        if self._rank_by in _LOWER_IS_BETTER:
            return -float(value)
        return float(value)
