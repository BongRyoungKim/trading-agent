"""
Backtest performance gate: decides whether a candidate parameter set is
safe to promote to live trading, by comparing its backtested metrics
against the metrics of what is currently live.

Pure comparison logic only — no I/O, no backtest execution (that is
src/backtest/param_validator.py's job). Metrics dicts are whatever
src.backtest.metrics.compute_all() returns.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class GateThresholds:
    """
    Default thresholds are deliberately permissive-but-safe: a candidate
    does not need to be strictly better everywhere, just not meaningfully
    worse, while still requiring a minimum sample size to trust the
    comparison at all.
    """

    min_profit_factor_ratio: float = 0.95    # candidate PF >= baseline PF * 0.95
    min_sharpe_ratio_ratio: float = 0.90      # candidate Sharpe >= baseline Sharpe * 0.90
    max_drawdown_ratio: float = 1.10          # candidate MDD <= baseline MDD * 1.10
    max_win_rate_drop_pct: float = 5.0        # candidate win rate >= baseline - 5 (percentage points)
    min_trades: int = 20                      # both baseline and candidate need >= this many trades


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PerformanceGateResult:
    passed: bool
    checks: tuple[CheckResult, ...]

    def summary(self) -> str:
        lines = [f"{'PASS' if self.passed else 'FAIL'} overall"]
        for c in self.checks:
            lines.append(f"  [{'OK' if c.passed else 'FAIL'}] {c.name}: {c.detail}")
        return "\n".join(lines)


def _check_min_trades(name: str, n: int, min_n: int) -> CheckResult:
    passed = n >= min_n
    return CheckResult(name, passed, f"n={n} (need >= {min_n})")


def _check_ratio_floor(name: str, baseline: float, candidate: float, ratio: float) -> CheckResult:
    """candidate must be >= baseline * ratio. Handles the profit_factor=inf
    edge case (baseline with zero losing trades): only an also-infinite
    candidate can clear that bar."""
    if math.isinf(baseline):
        passed = math.isinf(candidate) and candidate > 0
        return CheckResult(
            name, passed,
            f"baseline is inf (no losing trades) -> candidate must also be inf, got {candidate}",
        )
    required = baseline * ratio
    passed = candidate >= required
    return CheckResult(
        name, passed,
        f"candidate={candidate:.3f} >= required={required:.3f} (baseline={baseline:.3f} x {ratio})",
    )


def _check_mdd_ceiling(baseline_mdd: float, candidate_mdd: float, ratio: float) -> CheckResult:
    """candidate drawdown must not exceed baseline drawdown * ratio (lower is better)."""
    required = baseline_mdd * ratio
    passed = candidate_mdd <= required
    return CheckResult(
        "max_drawdown_pct", passed,
        f"candidate={candidate_mdd:.2f}% <= required={required:.2f}% (baseline={baseline_mdd:.2f}% x {ratio})",
    )


def _check_win_rate_floor(baseline_wr: float, candidate_wr: float, max_drop_pct: float) -> CheckResult:
    required = baseline_wr - max_drop_pct
    passed = candidate_wr >= required
    return CheckResult(
        "win_rate_pct", passed,
        f"candidate={candidate_wr:.1f}% >= required={required:.1f}% (baseline={baseline_wr:.1f}% - {max_drop_pct}pp)",
    )


def evaluate_performance_gate(
    baseline: dict,
    candidate: dict,
    thresholds: GateThresholds = GateThresholds(),
) -> PerformanceGateResult:
    """
    baseline/candidate: dicts shaped like src.backtest.metrics.compute_all()'s
    return value (must contain total_trades, profit_factor, sharpe_ratio,
    max_drawdown_pct, win_rate_pct).
    """
    checks = (
        _check_min_trades("baseline_trades", baseline["total_trades"], thresholds.min_trades),
        _check_min_trades("candidate_trades", candidate["total_trades"], thresholds.min_trades),
        _check_ratio_floor(
            "profit_factor", baseline["profit_factor"], candidate["profit_factor"],
            thresholds.min_profit_factor_ratio,
        ),
        _check_ratio_floor(
            "sharpe_ratio", baseline["sharpe_ratio"], candidate["sharpe_ratio"],
            thresholds.min_sharpe_ratio_ratio,
        ),
        _check_mdd_ceiling(
            baseline["max_drawdown_pct"], candidate["max_drawdown_pct"], thresholds.max_drawdown_ratio,
        ),
        _check_win_rate_floor(
            baseline["win_rate_pct"], candidate["win_rate_pct"], thresholds.max_win_rate_drop_pct,
        ),
    )
    return PerformanceGateResult(passed=all(c.passed for c in checks), checks=checks)
