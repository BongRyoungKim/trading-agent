"""Unit tests for the backtest performance gate."""
from __future__ import annotations

from src.backtest.performance_gate import GateThresholds, evaluate_performance_gate


def _metrics(
    total_trades=30, profit_factor=1.0, sharpe_ratio=0.5,
    max_drawdown_pct=10.0, win_rate_pct=50.0,
) -> dict:
    return {
        "total_trades": total_trades,
        "profit_factor": profit_factor,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown_pct": max_drawdown_pct,
        "win_rate_pct": win_rate_pct,
    }


class TestEvaluatePerformanceGate:
    def test_identical_metrics_pass(self):
        m = _metrics()
        result = evaluate_performance_gate(m, m)
        assert result.passed is True
        assert all(c.passed for c in result.checks)

    def test_strictly_better_candidate_passes(self):
        baseline = _metrics(profit_factor=1.0, sharpe_ratio=0.5, max_drawdown_pct=10.0, win_rate_pct=50.0)
        candidate = _metrics(profit_factor=1.2, sharpe_ratio=0.6, max_drawdown_pct=8.0, win_rate_pct=55.0)
        result = evaluate_performance_gate(baseline, candidate)
        assert result.passed is True

    def test_candidate_below_pf_ratio_fails(self):
        baseline = _metrics(profit_factor=1.0)
        candidate = _metrics(profit_factor=0.9)  # < 1.0 * 0.95
        result = evaluate_performance_gate(baseline, candidate)
        assert result.passed is False
        pf_check = next(c for c in result.checks if c.name == "profit_factor")
        assert pf_check.passed is False

    def test_candidate_at_exact_pf_ratio_boundary_passes(self):
        baseline = _metrics(profit_factor=1.0)
        candidate = _metrics(profit_factor=0.95)  # == 1.0 * 0.95, boundary is inclusive
        result = evaluate_performance_gate(baseline, candidate)
        pf_check = next(c for c in result.checks if c.name == "profit_factor")
        assert pf_check.passed is True

    def test_candidate_below_sharpe_ratio_fails(self):
        baseline = _metrics(sharpe_ratio=1.0)
        candidate = _metrics(sharpe_ratio=0.8)  # < 1.0 * 0.90
        result = evaluate_performance_gate(baseline, candidate)
        assert result.passed is False

    def test_candidate_mdd_too_much_worse_fails(self):
        baseline = _metrics(max_drawdown_pct=10.0)
        candidate = _metrics(max_drawdown_pct=12.0)  # > 10.0 * 1.10
        result = evaluate_performance_gate(baseline, candidate)
        assert result.passed is False
        mdd_check = next(c for c in result.checks if c.name == "max_drawdown_pct")
        assert mdd_check.passed is False

    def test_candidate_mdd_within_ratio_passes(self):
        baseline = _metrics(max_drawdown_pct=10.0)
        candidate = _metrics(max_drawdown_pct=11.0)  # == 10.0 * 1.10, boundary inclusive
        result = evaluate_performance_gate(baseline, candidate)
        mdd_check = next(c for c in result.checks if c.name == "max_drawdown_pct")
        assert mdd_check.passed is True

    def test_candidate_win_rate_drop_too_large_fails(self):
        baseline = _metrics(win_rate_pct=50.0)
        candidate = _metrics(win_rate_pct=44.0)  # 6pp drop > 5pp max
        result = evaluate_performance_gate(baseline, candidate)
        assert result.passed is False

    def test_candidate_win_rate_drop_within_bound_passes(self):
        baseline = _metrics(win_rate_pct=50.0)
        candidate = _metrics(win_rate_pct=45.0)  # exactly 5pp drop, boundary inclusive
        result = evaluate_performance_gate(baseline, candidate)
        wr_check = next(c for c in result.checks if c.name == "win_rate_pct")
        assert wr_check.passed is True

    def test_baseline_below_min_trades_fails(self):
        baseline = _metrics(total_trades=5)
        candidate = _metrics(total_trades=30)
        result = evaluate_performance_gate(baseline, candidate)
        assert result.passed is False
        assert any(c.name == "baseline_trades" and not c.passed for c in result.checks)

    def test_candidate_below_min_trades_fails(self):
        baseline = _metrics(total_trades=30)
        candidate = _metrics(total_trades=5)
        result = evaluate_performance_gate(baseline, candidate)
        assert result.passed is False
        assert any(c.name == "candidate_trades" and not c.passed for c in result.checks)

    def test_infinite_baseline_pf_requires_infinite_candidate(self):
        baseline = _metrics(profit_factor=float("inf"))
        candidate = _metrics(profit_factor=5.0)
        result = evaluate_performance_gate(baseline, candidate)
        pf_check = next(c for c in result.checks if c.name == "profit_factor")
        assert pf_check.passed is False

    def test_infinite_baseline_and_candidate_pf_passes(self):
        baseline = _metrics(profit_factor=float("inf"))
        candidate = _metrics(profit_factor=float("inf"))
        result = evaluate_performance_gate(baseline, candidate)
        pf_check = next(c for c in result.checks if c.name == "profit_factor")
        assert pf_check.passed is True

    def test_custom_thresholds_are_honoured(self):
        baseline = _metrics(profit_factor=1.0)
        candidate = _metrics(profit_factor=0.5)  # would normally fail
        lenient = GateThresholds(min_profit_factor_ratio=0.4)
        result = evaluate_performance_gate(baseline, candidate, lenient)
        pf_check = next(c for c in result.checks if c.name == "profit_factor")
        assert pf_check.passed is True

    def test_summary_includes_all_check_names(self):
        m = _metrics()
        result = evaluate_performance_gate(m, m)
        summary = result.summary()
        for check in result.checks:
            assert check.name in summary
