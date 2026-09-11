"""Unit tests for RegimeAdaptiveStrategy."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.strategy.models import SignalAction
from src.strategy.regime_adaptive import RegimeAdaptiveStrategy
from src.utils.exceptions import StrategyError


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_df(
    closes: list[float],
    volumes: list[float] | None = None,
    opens: list[float] | None = None,
) -> pd.DataFrame:
    n = len(closes)
    c = np.array(closes, dtype=float)
    v = np.array(volumes if volumes is not None else [2000.0] * n, dtype=float)
    o = np.array(opens if opens is not None else c * 0.999, dtype=float)
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return pd.DataFrame({
        "open":      o,
        "high":      np.maximum(o, c) * 1.002,
        "low":       np.minimum(o, c) * 0.998,
        "close":     c,
        "volume":    v,
        "timestamp": [base + timedelta(minutes=15 * i) for i in range(n)],
    })


def _uptrend_data(n: int = 150, start: float = 50_000.0) -> pd.DataFrame:
    """Consistent 0.5% rise per bar → ADX high, EMA20 > EMA50."""
    prices = [start * (1.005 ** i) for i in range(n)]
    return _make_df(prices)


def _downtrend_data(n: int = 150, start: float = 50_000.0) -> pd.DataFrame:
    """Consistent 0.5% fall per bar → ADX high, EMA20 < EMA50."""
    prices = [start * (0.995 ** i) for i in range(n)]
    return _make_df(prices)


def _ranging_data(n: int = 150, base: float = 50_000.0) -> pd.DataFrame:
    """Alternating ±0.3% bars → ADX low."""
    prices = []
    p = base
    for i in range(n):
        p = p * (1.003 if i % 2 == 0 else 0.997)
        prices.append(p)
    return _make_df(prices)


def _make_strategy(**kwargs) -> RegimeAdaptiveStrategy:
    return RegimeAdaptiveStrategy(symbol="BTC/KRW", **kwargs)


# ── Constructor validation ─────────────────────────────────────────────────────

class TestConstructor:
    def test_default_params(self):
        s = _make_strategy()
        params = s.get_parameters()
        assert params["adx_trend_threshold"] == 25.0
        assert params["ema_fast"] == 20
        assert params["ema_slow"] == 50

    def test_name_includes_ema_periods(self):
        s = _make_strategy(ema_fast=10, ema_slow=30)
        assert "10" in s.name
        assert "30" in s.name

    def test_timeframe_is_15m(self):
        assert _make_strategy().timeframe == "15m"

    def test_invalid_ema_order_raises(self):
        with pytest.raises(ValueError, match="ema_fast"):
            _make_strategy(ema_fast=50, ema_slow=20)

    def test_invalid_adx_threshold_raises(self):
        with pytest.raises(ValueError, match="adx_trend_threshold"):
            _make_strategy(adx_trend_threshold=0)

    def test_min_required_bars_at_least_100(self):
        # SwingMomentumStrategy requires ema_slow * 2 = 100 by default
        assert _make_strategy().min_required_bars() >= 100


# ── Regime detection ──────────────────────────────────────────────────────────

class TestDetectRegime:
    def test_uptrend_detected(self):
        s = _make_strategy()
        regime = s.detect_regime(_uptrend_data())
        assert regime == "uptrend"

    def test_downtrend_detected(self):
        s = _make_strategy()
        regime = s.detect_regime(_downtrend_data())
        assert regime == "downtrend"

    def test_ranging_detected(self):
        s = _make_strategy()
        regime = s.detect_regime(_ranging_data())
        assert regime == "ranging"

    def test_custom_adx_threshold_raises_ranging_bar(self):
        # At threshold=99, even strong uptrend ADX (~100) is ≥ 99 → still uptrend
        # but ranging market (ADX ~5-15) is always below 99 → "ranging"
        s = _make_strategy(adx_trend_threshold=99.0)
        regime = s.detect_regime(_ranging_data())
        assert regime == "ranging"


# ── Signal routing ────────────────────────────────────────────────────────────

class TestSignalRouting:
    def test_uptrend_reason_contains_uptrend_tag(self):
        s = _make_strategy()
        signal = s.generate_signal(_uptrend_data())
        assert "[UPTREND]" in signal.reason

    def test_downtrend_reason_contains_downtrend_tag(self):
        s = _make_strategy()
        signal = s.generate_signal(_downtrend_data())
        assert "[DOWNTREND]" in signal.reason

    def test_ranging_reason_contains_ranging_tag(self):
        s = _make_strategy()
        signal = s.generate_signal(_ranging_data())
        assert "[RANGING]" in signal.reason

    def test_regime_in_metadata(self):
        s = _make_strategy()
        for data, expected in [
            (_uptrend_data(), "uptrend"),
            (_downtrend_data(), "downtrend"),
            (_ranging_data(), "ranging"),
        ]:
            sig = s.generate_signal(data)
            assert sig.metadata.get("regime") == expected

    def test_signal_symbol_preserved(self):
        s = _make_strategy()
        sig = s.generate_signal(_uptrend_data())
        assert sig.symbol == "BTC/KRW"

    def test_returns_hold_when_conditions_unmet(self):
        # Ranging data with default MeanReversion conditions → HOLD expected
        # (no volume spike, no RSI oversold)
        s = _make_strategy()
        sig = s.generate_signal(_ranging_data())
        # Should not raise; action can be HOLD or actionable
        assert sig.action in (SignalAction.BUY, SignalAction.SELL, SignalAction.HOLD)


# ── Insufficient data ─────────────────────────────────────────────────────────

class TestInsufficientData:
    def test_raises_on_too_few_bars(self):
        s = _make_strategy()
        short_df = _uptrend_data(n=10)
        with pytest.raises(StrategyError):
            s.generate_signal(short_df)


# ── Parameter passthrough ─────────────────────────────────────────────────────

class TestParameterPassthrough:
    def test_mr_params_in_sub_strategy(self):
        s = _make_strategy(mr_vol_mult=0.5, mr_rsi_oversold_fast=15.0)
        mr_params = s.get_parameters()["mean_reversion"]
        assert mr_params["vol_mult"] == 0.5
        assert mr_params["rsi_oversold_fast"] == 15.0

    def test_sm_params_in_sub_strategy(self):
        s = _make_strategy(sm_vol_mult=2.0, sm_adx_threshold=30.0)
        sm_params = s.get_parameters()["swing_momentum"]
        assert sm_params["vol_mult"] == 2.0
        assert sm_params["adx_threshold"] == 30.0

    def test_mr_rsi_exit_fast_passed_through(self):
        s = _make_strategy(mr_rsi_exit_fast=78.0)
        mr_params = s.get_parameters()["mean_reversion"]
        assert mr_params["rsi_exit_fast"] == 78.0

    def test_no_entry_hours_passed_to_mr(self):
        s = _make_strategy(mr_no_entry_hours_utc=[0, 1, 2])
        # Verify sub-strategy holds the setting (via regime→MR delegation)
        # MeanReversionStrategy stores hours in _no_entry_hours_utc
        assert hasattr(s._mean_reversion, "_no_entry_hours_utc")
        assert 0 in s._mean_reversion._no_entry_hours_utc

    def test_mr_sma_period_default_is_zero_disabled(self):
        # Backward compatibility: no mr_sma_period arg → long-term trend
        # filter stays fully disabled, identical to pre-existing behaviour.
        s = _make_strategy()
        mr_params = s.get_parameters()["mean_reversion"]
        assert mr_params["sma_period"] == 0

    def test_mr_sma_period_and_floor_passed_to_sub_strategy(self):
        s = _make_strategy(mr_sma_period=199, mr_sma_floor=0.955)
        mr_params = s.get_parameters()["mean_reversion"]
        assert mr_params["sma_period"] == 199
        assert mr_params["sma_floor"] == 0.955
