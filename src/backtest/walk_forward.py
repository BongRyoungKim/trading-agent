"""
Walk-Forward Analysis.

Validates strategy robustness by repeatedly training on an in-sample window
and testing on the immediately following out-of-sample window.

Terminology:
    in-sample  (IS)  — window used to run / calibrate the strategy.
    out-of-sample (OOS) — the following window used to evaluate performance.
    anchored=False   — rolling windows (both IS and OOS slide forward).
    anchored=True    — expanding IS window (grows each fold; OOS stays fixed).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterator

import pandas as pd
from loguru import logger

from src.backtest.metrics import compute_all
from src.backtest.models import BacktestResult
from src.backtest.runner import BacktestConfig, BacktestRunner
from src.strategy.base import BaseStrategy


@dataclass(frozen=True)
class WalkForwardFold:
    """Single IS/OOS window result."""

    fold_index: int
    is_start: pd.Timestamp
    is_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp
    is_result: BacktestResult
    oos_result: BacktestResult

    @property
    def oos_return_pct(self) -> float:
        return self.oos_result.total_return_pct

    @property
    def is_return_pct(self) -> float:
        return self.is_result.total_return_pct


@dataclass
class WalkForwardReport:
    """Aggregated results across all folds."""

    folds: list[WalkForwardFold] = field(default_factory=list)

    @property
    def n_folds(self) -> int:
        return len(self.folds)

    @property
    def avg_oos_return_pct(self) -> float:
        if not self.folds:
            return 0.0
        return sum(f.oos_return_pct for f in self.folds) / len(self.folds)

    @property
    def avg_is_return_pct(self) -> float:
        if not self.folds:
            return 0.0
        return sum(f.is_return_pct for f in self.folds) / len(self.folds)

    @property
    def oos_win_rate_pct(self) -> float:
        """Percentage of OOS folds that are profitable."""
        if not self.folds:
            return 0.0
        winners = sum(1 for f in self.folds if f.oos_return_pct > 0)
        return winners / len(self.folds) * 100

    @property
    def efficiency_ratio(self) -> float:
        """
        OOS avg return / IS avg return.
        A ratio close to 1.0 indicates the strategy generalises well.
        Values < 0 indicate OOS was loss-making despite profitable IS runs.
        """
        if self.avg_is_return_pct == 0:
            return 0.0
        return self.avg_oos_return_pct / self.avg_is_return_pct

    def summary(self) -> str:
        lines = [
            f"Walk-Forward Analysis — {self.n_folds} folds",
            f"  Avg IS  return : {self.avg_is_return_pct:+.2f}%",
            f"  Avg OOS return : {self.avg_oos_return_pct:+.2f}%",
            f"  OOS win rate   : {self.oos_win_rate_pct:.1f}%",
            f"  Efficiency     : {self.efficiency_ratio:.3f}",
        ]
        for fold in self.folds:
            lines.append(
                f"  Fold {fold.fold_index:02d}  IS {fold.is_start.date()}→{fold.is_end.date()} "
                f"({fold.is_return_pct:+.2f}%)  |  "
                f"OOS {fold.oos_start.date()}→{fold.oos_end.date()} "
                f"({fold.oos_return_pct:+.2f}%)"
            )
        return "\n".join(lines)


class WalkForwardAnalyzer:
    """
    Runs walk-forward analysis on a strategy over a historical OHLCV DataFrame.

    Usage:
        wfa = WalkForwardAnalyzer(
            strategy=strategy,
            config=BacktestConfig(),
            is_bars=200,
            oos_bars=50,
            anchored=False,
        )
        report = wfa.run(ohlcv_df)
        print(report.summary())
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        config: BacktestConfig | None = None,
        is_bars: int = 200,
        oos_bars: int = 50,
        anchored: bool = False,
    ) -> None:
        if is_bars <= 0 or oos_bars <= 0:
            raise ValueError("is_bars and oos_bars must be positive integers")
        if is_bars <= oos_bars:
            raise ValueError("is_bars must be greater than oos_bars")
        self._strategy = strategy
        self._config = config or BacktestConfig()
        self._is_bars = is_bars
        self._oos_bars = oos_bars
        self._anchored = anchored

    def run(self, data: pd.DataFrame) -> WalkForwardReport:
        """
        Execute walk-forward analysis.

        Args:
            data: OHLCV DataFrame (oldest-first, DatetimeIndex).

        Returns:
            WalkForwardReport with per-fold and aggregate statistics.
        """
        report = WalkForwardReport()
        runner = BacktestRunner(self._strategy, self._config)

        for fold_idx, (is_df, oos_df) in enumerate(self._windows(data)):
            if len(is_df) < self._strategy.min_required_bars():
                logger.debug("Skipping fold — insufficient IS bars", fold=fold_idx)
                continue
            if len(oos_df) < self._strategy.min_required_bars():
                logger.debug("Skipping fold — insufficient OOS bars", fold=fold_idx)
                continue

            is_result = runner.run(is_df)
            oos_result = runner.run(oos_df)

            fold = WalkForwardFold(
                fold_index=fold_idx,
                is_start=is_df.index[0],
                is_end=is_df.index[-1],
                oos_start=oos_df.index[0],
                oos_end=oos_df.index[-1],
                is_result=is_result,
                oos_result=oos_result,
            )
            report.folds.append(fold)
            logger.debug(
                "Fold complete",
                fold=fold_idx,
                is_return=f"{fold.is_return_pct:+.2f}%",
                oos_return=f"{fold.oos_return_pct:+.2f}%",
            )

        logger.info(
            "Walk-forward complete",
            folds=report.n_folds,
            avg_oos_return=f"{report.avg_oos_return_pct:+.2f}%",
            efficiency=f"{report.efficiency_ratio:.3f}",
        )
        return report

    # ── Window generation ─────────────────────────────────────────────────────

    def _windows(self, data: pd.DataFrame) -> Iterator[tuple[pd.DataFrame, pd.DataFrame]]:
        """Yield (is_window, oos_window) slices."""
        n = len(data)
        step = self._oos_bars  # advance by one OOS window per fold

        # is_end tracks where the IS window ends; grows in anchored mode
        is_end_idx = self._is_bars

        while True:
            oos_end_idx = is_end_idx + self._oos_bars
            if oos_end_idx > n:
                break

            if self._anchored:
                # Expanding IS: always start from bar 0
                is_start_idx = 0
            else:
                # Rolling: IS window slides at the same pace as OOS
                is_start_idx = is_end_idx - self._is_bars

            is_slice = data.iloc[is_start_idx:is_end_idx].copy()
            oos_slice = data.iloc[is_end_idx:oos_end_idx].copy()

            yield is_slice, oos_slice

            # Slide OOS (and IS end) forward by one step
            is_end_idx += step
