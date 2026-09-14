"""
Sizing-sensitive portfolio metrics.

src/backtest/metrics.py computes profit factor, win rate, Sharpe and drawdown
from per-trade percentage returns or fixed-unit (amount=1) PnL — all of which
are *invariant* under a uniform change in position size. They therefore cannot
be used to evaluate a position-sizing change at all. This module adds the three
numbers that do move with sizing, kept deliberately small (see the sizing
research scope: individual account, 4-5 concurrent symbols):

  1. equity_max_drawdown_pct — worst peak-to-trough of the portfolio equity path
  2. worst_loss_streak       — deepest run of consecutive losing trades
  3. bootstrap_sizing_paths  — resampled trade orderings, to separate "this
     sizing is fine" from "this sample was lucky"

Pure functions only — no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class LossStreak:
    """Deepest run of consecutive losing trades."""

    length: int
    decline: Decimal      # summed PnL over the streak (negative)
    decline_pct: float    # decline as % of the equity it started from


@dataclass(frozen=True)
class BootstrapResult:
    """Distribution of outcomes over resampled trade orderings."""

    n_paths: int
    final_return_p5: float
    final_return_median: float
    final_return_p95: float
    max_drawdown_p95: float
    prob_ruin: float          # P(equity falls >= ruin_threshold_pct below its peak)
    ruin_threshold_pct: float


def equity_max_drawdown_pct(equity: Sequence[Decimal | float]) -> float:
    """
    Worst peak-to-trough decline of an equity path, as a positive percentage.
    Returns 0.0 for empty/single-point paths or non-positive peaks.
    """
    peak = None
    worst = 0.0
    for value in equity:
        v = float(value)
        if peak is None or v > peak:
            peak = v
        if peak is not None and peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst * 100


def worst_loss_streak(
    pnls: Sequence[Decimal], equity_at_streak_start: Decimal
) -> LossStreak:
    """
    Find the run of consecutive losing trades with the largest total loss.

    `equity_at_streak_start` is the capital the percentage is measured against
    — pass the portfolio equity at the moment the streak began (or the initial
    capital for a conservative single-reference figure).
    """
    best_len = 0
    best_decline = Decimal("0")
    run_len = 0
    run_decline = Decimal("0")

    for pnl in pnls:
        if pnl <= 0:
            run_len += 1
            run_decline += pnl
            if run_decline < best_decline:
                best_decline = run_decline
                best_len = run_len
        else:
            run_len = 0
            run_decline = Decimal("0")

    pct = (
        float(best_decline / equity_at_streak_start * 100)
        if equity_at_streak_start > 0
        else 0.0
    )
    return LossStreak(length=best_len, decline=best_decline, decline_pct=pct)


def _path_stats(returns: np.ndarray, pct: float) -> tuple[float, float]:
    """Compound one resampled path; return (final_return_pct, max_drawdown_pct)."""
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for r in returns:
        equity *= 1.0 + pct * float(r)
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak)
    return (equity - 1.0) * 100, worst * 100


def bootstrap_sizing_paths(
    trade_returns: Sequence[float],
    position_size_pct: float,
    n_paths: int = 2000,
    seed: int = 42,
    ruin_threshold_pct: float = 20.0,
) -> BootstrapResult:
    """
    Resample the realised per-trade returns with replacement and compound each
    path at `position_size_pct` of equity, to see how much of the observed
    outcome is sequence luck.

    Deliberately *not* a full Monte-Carlo: the return distribution is not
    modelled, only reordered/resampled. With a sample of ~50 trades that is
    the most the data can honestly support.

    Raises:
        ValueError: on empty returns or non-positive n_paths.
    """
    if not trade_returns:
        raise ValueError("trade_returns must not be empty")
    if n_paths <= 0:
        raise ValueError(f"n_paths must be positive, got {n_paths}")

    rng = np.random.default_rng(seed)
    source = np.asarray(trade_returns, dtype=float)
    finals: list[float] = []
    drawdowns: list[float] = []

    for _ in range(n_paths):
        sample = rng.choice(source, size=source.size, replace=True)
        final, mdd = _path_stats(sample, position_size_pct)
        finals.append(final)
        drawdowns.append(mdd)

    dd = np.asarray(drawdowns)
    return BootstrapResult(
        n_paths=n_paths,
        final_return_p5=float(np.percentile(finals, 5)),
        final_return_median=float(np.percentile(finals, 50)),
        final_return_p95=float(np.percentile(finals, 95)),
        max_drawdown_p95=float(np.percentile(dd, 95)),
        prob_ruin=float((dd >= ruin_threshold_pct).mean()),
        ruin_threshold_pct=ruin_threshold_pct,
    )
