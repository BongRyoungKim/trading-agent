"""
Runs a baseline-vs-candidate backtest through the shared exit-rule engine
(src/risk/exit_rules.py, src/backtest/exit_backtest_engine.py) and scores
the result with the performance gate (src/backtest/performance_gate.py).

Meant to be invoked manually (via scripts/validate_params.py) before any
strategy/risk parameter change reaches live trading. Running a full
backtest here is deliberately NOT triggered automatically inside the live
container — the production VM is a 1GB-RAM free-tier instance and this
kind of workload should run on a machine with headroom (see
docs/research-protocol.md).
"""
from __future__ import annotations

import types
from dataclasses import dataclass
from decimal import Decimal

from src.backtest.data_cache import load_cached_ohlcv
from src.backtest.data_split import split_time_ordered
from src.backtest.exit_backtest_engine import ExitBacktestConfig, run_exit_backtest
from src.backtest.metrics import compute_all
from src.backtest.models import Trade
from src.backtest.performance_gate import (
    GateThresholds,
    PerformanceGateResult,
    evaluate_performance_gate,
)
from src.risk.manager import PortfolioState, RiskManager
from src.strategy.regime_adaptive import RegimeAdaptiveStrategy

DEFAULT_INITIAL_CAPITAL = Decimal("10000000")


@dataclass(frozen=True)
class ValidationResult:
    gate: PerformanceGateResult
    baseline_metrics: dict
    candidate_metrics: dict

    def report(self) -> str:
        def _line(label: str, m: dict) -> str:
            return (
                f"{label}: PF={m['profit_factor']:.3f}  Sharpe={m['sharpe_ratio']:.3f}  "
                f"MDD={m['max_drawdown_pct']:.2f}%  WR={m['win_rate_pct']:.1f}%  "
                f"n={m['total_trades']}"
            )

        return "\n".join([
            self.gate.summary(),
            "",
            _line("baseline ", self.baseline_metrics),
            _line("candidate", self.candidate_metrics),
        ])


def _make_risk_manager(initial_capital: Decimal) -> RiskManager:
    # RiskManager only reads settings.max_position_risk here (as a fallback
    # when calculate_stop_loss() gets no ATR value) — a full Settings object
    # needs exchange credentials/env vars this standalone validator has no
    # business requiring, so a minimal stand-in is used instead of MagicMock
    # (this is production code, not a test).
    settings = types.SimpleNamespace(max_position_risk=0.02)
    state = PortfolioState(capital=initial_capital, peak_capital=initial_capital)
    return RiskManager(settings, state)  # type: ignore[arg-type]


def _backtest_trades(
    symbol: str,
    timeframe: str,
    data_split_part: str,
    strategy_kwargs: dict,
    risk_config: ExitBacktestConfig,
    initial_capital: Decimal,
) -> tuple[list[Trade], object, object]:
    df = load_cached_ohlcv(symbol, timeframe)
    split = split_time_ordered(df)
    df_part = getattr(split, data_split_part)

    strategy = RegimeAdaptiveStrategy(symbol=symbol, **strategy_kwargs)
    risk_manager = _make_risk_manager(initial_capital)
    trades = run_exit_backtest(df_part, strategy, risk_manager, risk_config, symbol=symbol)
    return trades, df_part.timestamp.iloc[0], df_part.timestamp.iloc[-1]


def validate_param_change(
    symbols: list[str],
    baseline_strategy_kwargs: dict,
    baseline_risk_config: ExitBacktestConfig,
    candidate_strategy_kwargs: dict,
    candidate_risk_config: ExitBacktestConfig,
    timeframe: str = "15m",
    data_split_part: str = "validation",
    thresholds: GateThresholds = GateThresholds(),
    initial_capital: Decimal = DEFAULT_INITIAL_CAPITAL,
) -> ValidationResult:
    """
    Backtests `symbols` under both the baseline (currently-live) and
    candidate parameter sets over the same data_split_part, pools trades
    across all symbols, and scores the pooled result with the performance
    gate.

    data_split_part should be "validation" for routine checks; "holdout"
    is reserved for a final, one-time confirmation per
    docs/research-protocol.md and should not be used repeatedly while
    iterating on a candidate.
    """
    baseline_trades: list[Trade] = []
    candidate_trades: list[Trade] = []
    start = end = None

    for symbol in symbols:
        b_trades, b_start, b_end = _backtest_trades(
            symbol, timeframe, data_split_part, baseline_strategy_kwargs,
            baseline_risk_config, initial_capital,
        )
        baseline_trades.extend(b_trades)
        c_trades, _, _ = _backtest_trades(
            symbol, timeframe, data_split_part, candidate_strategy_kwargs,
            candidate_risk_config, initial_capital,
        )
        candidate_trades.extend(c_trades)
        if start is None:
            start, end = b_start, b_end

    baseline_metrics = compute_all(initial_capital, initial_capital, baseline_trades, start, end)
    candidate_metrics = compute_all(initial_capital, initial_capital, candidate_trades, start, end)
    gate_result = evaluate_performance_gate(baseline_metrics, candidate_metrics, thresholds)
    return ValidationResult(
        gate=gate_result, baseline_metrics=baseline_metrics, candidate_metrics=candidate_metrics,
    )
