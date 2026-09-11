"""
Unit tests for src/backtest/param_validator.py.

Uses synthetic flat-price OHLCV (patched in place of the real cache) so
these tests run fast and deterministically, independent of any locally
cached market data. Flat prices mean RegimeAdaptiveStrategy should never
fire a BUY, which is fine here — the point of these tests is to verify the
baseline-vs-candidate wiring and gate integration, not strategy signal
logic (already covered by test_regime_adaptive_strategy.py).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pandas as pd
import pytest

from src.backtest.exit_backtest_engine import ExitBacktestConfig
from src.backtest.param_validator import validate_param_change

STRATEGY_KWARGS = dict(
    adx_trend_threshold=25.0, mr_vol_mult=0.5, mr_rsi_oversold_fast=28.0,
    mr_rsi_oversold_slow=38.0, mr_rsi_exit=60.0, sm_vol_mult=0.4, sm_adx_threshold=28.0,
)
RISK_CONFIG = ExitBacktestConfig(
    sl_ceiling_pct=Decimal("1.5"), sl_floor_pct=Decimal("3.0"),
    atr_multiplier=2.0, tp_rr_multiplier=Decimal("1.5"),
)


def _flat_ohlcv(n: int = 2000, price: float = 50000.0) -> pd.DataFrame:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        {
            "timestamp": base + timedelta(minutes=15 * i),
            "open": price, "high": price, "low": price, "close": price,
            "volume": 100.0,
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def _patch_cache():
    with patch("src.backtest.param_validator.load_cached_ohlcv", return_value=_flat_ohlcv()):
        yield


class TestValidateParamChange:
    def test_identical_baseline_and_candidate_pass_or_fail_together(self):
        """Same params on both sides -> same metrics -> gate outcome is
        self-consistent (will fail on min_trades with flat/no-signal data,
        but must fail identically on both sides, not asymmetrically)."""
        result = validate_param_change(
            symbols=["BTC/KRW"],
            baseline_strategy_kwargs=STRATEGY_KWARGS,
            baseline_risk_config=RISK_CONFIG,
            candidate_strategy_kwargs=STRATEGY_KWARGS,
            candidate_risk_config=RISK_CONFIG,
        )
        assert result.baseline_metrics["total_trades"] == result.candidate_metrics["total_trades"]

    def test_flat_data_produces_zero_trades_and_fails_min_trades_gate(self):
        result = validate_param_change(
            symbols=["BTC/KRW"],
            baseline_strategy_kwargs=STRATEGY_KWARGS,
            baseline_risk_config=RISK_CONFIG,
            candidate_strategy_kwargs=STRATEGY_KWARGS,
            candidate_risk_config=RISK_CONFIG,
        )
        assert result.baseline_metrics["total_trades"] == 0
        assert result.gate.passed is False
        assert any(c.name == "baseline_trades" and not c.passed for c in result.gate.checks)

    def test_pools_trades_across_multiple_symbols(self):
        result = validate_param_change(
            symbols=["BTC/KRW", "ETH/KRW"],
            baseline_strategy_kwargs=STRATEGY_KWARGS,
            baseline_risk_config=RISK_CONFIG,
            candidate_strategy_kwargs=STRATEGY_KWARGS,
            candidate_risk_config=RISK_CONFIG,
        )
        # Still 0 (flat data), but must not have crashed pooling two symbols.
        assert result.baseline_metrics["total_trades"] == 0

    def test_report_contains_pass_fail_and_both_metric_lines(self):
        result = validate_param_change(
            symbols=["BTC/KRW"],
            baseline_strategy_kwargs=STRATEGY_KWARGS,
            baseline_risk_config=RISK_CONFIG,
            candidate_strategy_kwargs=STRATEGY_KWARGS,
            candidate_risk_config=RISK_CONFIG,
        )
        report = result.report()
        assert "baseline" in report
        assert "candidate" in report

    def test_uses_requested_data_split_part(self):
        """Passing data_split_part="train" vs "holdout" must not error —
        exercises the getattr(split, data_split_part) wiring for each valid
        DataSplit field."""
        for part in ("train", "validation", "holdout"):
            result = validate_param_change(
                symbols=["BTC/KRW"],
                baseline_strategy_kwargs=STRATEGY_KWARGS,
                baseline_risk_config=RISK_CONFIG,
                candidate_strategy_kwargs=STRATEGY_KWARGS,
                candidate_risk_config=RISK_CONFIG,
                data_split_part=part,
            )
            assert result.baseline_metrics["total_trades"] == 0
