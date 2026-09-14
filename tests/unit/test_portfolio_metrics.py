"""
Tests for src/backtest/portfolio_metrics.py — sizing-sensitive portfolio
metrics (the existing src/backtest/metrics.py numbers are sizing-invariant
and therefore cannot evaluate a position-sizing change).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.backtest.portfolio_metrics import (
    bootstrap_sizing_paths,
    equity_max_drawdown_pct,
    worst_loss_streak,
)


class TestEquityMaxDrawdown:
    def test_known_sequence(self) -> None:
        # peak 120 -> trough 90 = 25% drawdown
        equity = [Decimal("100"), Decimal("120"), Decimal("90"), Decimal("110")]
        assert equity_max_drawdown_pct(equity) == pytest.approx(25.0)

    def test_monotonic_rise_has_no_drawdown(self) -> None:
        assert equity_max_drawdown_pct([Decimal("100"), Decimal("110")]) == 0.0

    def test_empty_and_single_point(self) -> None:
        assert equity_max_drawdown_pct([]) == 0.0
        assert equity_max_drawdown_pct([Decimal("100")]) == 0.0

    def test_accepts_floats(self) -> None:
        assert equity_max_drawdown_pct([100.0, 50.0]) == pytest.approx(50.0)

    def test_non_positive_peak_is_ignored(self) -> None:
        assert equity_max_drawdown_pct([Decimal("0"), Decimal("0")]) == 0.0


class TestWorstLossStreak:
    def test_longest_losing_run_and_its_decline(self) -> None:
        # equity_at_entry 1000 for every trade; losses of -50, -30 back to back
        pnls = [Decimal("100"), Decimal("-50"), Decimal("-30"), Decimal("40")]
        streak = worst_loss_streak(pnls, equity_at_streak_start=Decimal("1000"))
        assert streak.length == 2
        assert streak.decline == Decimal("-80")
        assert streak.decline_pct == pytest.approx(-8.0)

    def test_no_losses(self) -> None:
        streak = worst_loss_streak([Decimal("10"), Decimal("20")],
                                   equity_at_streak_start=Decimal("1000"))
        assert streak.length == 0
        assert streak.decline == Decimal("0")
        assert streak.decline_pct == 0.0

    def test_picks_deepest_not_longest_when_they_differ(self) -> None:
        # run of three small losses (-3) vs run of two large ones (-50)
        pnls = [
            Decimal("-1"), Decimal("-1"), Decimal("-1"),
            Decimal("5"),
            Decimal("-50"), Decimal("-50"),
        ]
        streak = worst_loss_streak(pnls, equity_at_streak_start=Decimal("1000"))
        assert streak.length == 2
        assert streak.decline == Decimal("-100")

    def test_empty_input(self) -> None:
        streak = worst_loss_streak([], equity_at_streak_start=Decimal("1000"))
        assert streak.length == 0
        assert streak.decline_pct == 0.0

    def test_zero_start_equity_yields_zero_pct(self) -> None:
        streak = worst_loss_streak([Decimal("-10")], equity_at_streak_start=Decimal("0"))
        assert streak.decline_pct == 0.0


class TestBootstrapSizingPaths:
    RETURNS = [0.02, -0.01, 0.03, -0.02, 0.01, -0.015, 0.025, -0.005]

    def test_same_seed_is_reproducible(self) -> None:
        a = bootstrap_sizing_paths(self.RETURNS, position_size_pct=0.25,
                                   n_paths=200, seed=42)
        b = bootstrap_sizing_paths(self.RETURNS, position_size_pct=0.25,
                                   n_paths=200, seed=42)
        assert a == b

    def test_different_seed_changes_result(self) -> None:
        a = bootstrap_sizing_paths(self.RETURNS, position_size_pct=0.25,
                                   n_paths=200, seed=42)
        b = bootstrap_sizing_paths(self.RETURNS, position_size_pct=0.25,
                                   n_paths=200, seed=7)
        assert a != b

    def test_percentiles_are_ordered(self) -> None:
        r = bootstrap_sizing_paths(self.RETURNS, position_size_pct=0.25,
                                   n_paths=500, seed=1)
        assert r.final_return_p5 <= r.final_return_median <= r.final_return_p95

    def test_larger_sizing_widens_the_distribution(self) -> None:
        small = bootstrap_sizing_paths(self.RETURNS, position_size_pct=0.10,
                                       n_paths=500, seed=3)
        large = bootstrap_sizing_paths(self.RETURNS, position_size_pct=0.50,
                                       n_paths=500, seed=3)
        assert large.max_drawdown_p95 > small.max_drawdown_p95

    def test_all_positive_returns_never_hit_ruin_threshold(self) -> None:
        r = bootstrap_sizing_paths([0.01, 0.02, 0.03], position_size_pct=0.5,
                                   n_paths=100, seed=5, ruin_threshold_pct=20.0)
        assert r.prob_ruin == 0.0
        assert r.max_drawdown_p95 == 0.0

    def test_severe_losses_raise_ruin_probability(self) -> None:
        r = bootstrap_sizing_paths([-0.30, -0.25, -0.20], position_size_pct=1.0,
                                   n_paths=100, seed=5, ruin_threshold_pct=20.0)
        assert r.prob_ruin == 1.0

    def test_empty_returns_rejected(self) -> None:
        with pytest.raises(ValueError):
            bootstrap_sizing_paths([], position_size_pct=0.25, n_paths=10, seed=1)

    def test_non_positive_paths_rejected(self) -> None:
        with pytest.raises(ValueError):
            bootstrap_sizing_paths([0.01], position_size_pct=0.25, n_paths=0, seed=1)
