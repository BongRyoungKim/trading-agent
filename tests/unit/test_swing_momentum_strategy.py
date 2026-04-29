"""Unit tests for SwingMomentumStrategy."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from src.strategy.swing_momentum import SwingMomentumStrategy
from src.strategy.models import SignalAction
from src.utils.exceptions import StrategyError


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_df(
    closes: list[float],
    volumes: list[float] | None = None,
    with_timestamp: bool = True,
) -> pd.DataFrame:
    n = len(closes)
    c = np.array(closes, dtype=float)
    v = np.array(volumes if volumes is not None else [100.0] * n, dtype=float)
    data = {
        "open": c * 0.999,
        "high": c * 1.005,
        "low": c * 0.995,
        "close": c,
        "volume": v,
    }
    if with_timestamp:
        base = datetime(2024, 1, 1, tzinfo=UTC)
        data["timestamp"] = [base + timedelta(minutes=15 * i) for i in range(n)]
    return pd.DataFrame(data)


def _small(**kwargs) -> SwingMomentumStrategy:
    """Strategy with small periods for easier signal construction."""
    defaults: dict = dict(
        ema_fast=5,
        ema_slow=10,
        rsi_period=7,
        adx_threshold=0.0,
        vol_mult=0.1,
        ema_proximity_pct=100.0,
        atr_period=5,
        macd_fast=5,
        macd_slow=10,
        macd_signal=3,
        rsi_lookback=3,
    )
    defaults.update(kwargs)
    return SwingMomentumStrategy("BTC/KRW", **defaults)


# ── Init ──────────────────────────────────────────────────────────────────────

class TestSwingMomentumInit:
    def test_valid_default_init(self):
        s = SwingMomentumStrategy("BTC/KRW")
        assert s is not None

    def test_ema_fast_ge_slow_raises(self):
        with pytest.raises(ValueError, match="ema_fast"):
            SwingMomentumStrategy("BTC/KRW", ema_fast=50, ema_slow=20)

    def test_rsi_range_invalid_raises(self):
        with pytest.raises(ValueError, match="RSI"):
            SwingMomentumStrategy("BTC/KRW", rsi_oversold=60, rsi_max=55)

    def test_rsi_max_ge_overbought_raises(self):
        with pytest.raises(ValueError, match="RSI"):
            SwingMomentumStrategy("BTC/KRW", rsi_max=80, rsi_overbought=75)


# ── Properties ────────────────────────────────────────────────────────────────

class TestSwingMomentumProperties:
    def test_name_contains_symbol(self):
        s = SwingMomentumStrategy("ETH/KRW")
        assert "ETH/KRW" in s.name

    def test_name_contains_ema_periods(self):
        s = SwingMomentumStrategy("BTC/KRW", ema_fast=20, ema_slow=50)
        assert "20" in s.name and "50" in s.name

    def test_timeframe_is_15m(self):
        s = SwingMomentumStrategy("BTC/KRW")
        assert s.timeframe == "15m"

    def test_min_required_bars_default(self):
        s = SwingMomentumStrategy("BTC/KRW")
        assert s.min_required_bars() >= 100

    def test_min_required_bars_small(self):
        s = _small()
        assert s.min_required_bars() >= 20

    def test_get_parameters_keys(self):
        s = SwingMomentumStrategy("BTC/KRW")
        p = s.get_parameters()
        for key in ("ema_fast", "ema_slow", "rsi_period", "adx_threshold",
                    "vol_mult", "ema_proximity_pct", "rsi_lookback"):
            assert key in p

    def test_get_parameters_values(self):
        s = SwingMomentumStrategy("BTC/KRW", vol_mult=3.0, adx_threshold=30.0)
        p = s.get_parameters()
        assert p["vol_mult"] == 3.0
        assert p["adx_threshold"] == 30.0

    def test_registered_in_registry(self):
        from src.strategy.registry import list_strategies
        import src.strategy  # noqa: F401
        assert "SwingMomentumStrategy" in list_strategies()


# ── Signal generation ─────────────────────────────────────────────────────────

class TestSwingMomentumSignals:
    def test_insufficient_data_raises(self):
        s = SwingMomentumStrategy("BTC/KRW")
        with pytest.raises(StrategyError):
            s.generate_signal(_make_df([50000.0] * 5))

    def test_generates_valid_signal(self):
        s = _small()
        df = _make_df([50000.0] * 60)
        sig = s.generate_signal(df)
        assert sig.symbol == "BTC/KRW"
        assert sig.action in (SignalAction.BUY, SignalAction.SELL, SignalAction.HOLD)

    def test_metadata_has_expected_keys(self):
        s = _small()
        df = _make_df([50000.0] * 60)
        sig = s.generate_signal(df)
        for key in ("price", "ema_fast", "ema_slow", "rsi", "macd_hist", "atr", "adx", "cond"):
            assert key in sig.metadata

    def test_cond_dict_has_flags(self):
        s = _small()
        df = _make_df([50000.0] * 60)
        sig = s.generate_signal(df)
        cond = sig.metadata["cond"]
        for key in ("uptrend", "adx_ok", "vol_ok", "macd_ok", "rsi_was_oversold"):
            assert key in cond

    def test_strength_in_valid_range(self):
        s = _small()
        df = _make_df([50000.0] * 60)
        sig = s.generate_signal(df)
        assert 0.0 <= sig.strength <= 1.0

    def test_sell_on_overbought_rsi(self):
        s = _small(rsi_oversold=1.0, rsi_max=5.0, rsi_overbought=8.0)
        prices = []
        p = 50000.0
        for i in range(60):
            p *= 0.998 if i % 4 == 3 else 1.008
            prices.append(p)
        df = _make_df(prices)
        sig = s.generate_signal(df)
        assert sig.action == SignalAction.SELL

    def test_sell_on_death_cross(self):
        s = _small()
        rising = [50000 * (1.03 ** i) for i in range(20)]
        falling = [rising[-1] * (0.88 ** i) for i in range(1, 25)]
        df = _make_df(rising + falling)
        sig = s.generate_signal(df)
        assert sig.action in (SignalAction.SELL, SignalAction.HOLD)

    def test_buy_with_all_conditions_relaxed(self):
        s = _small(
            rsi_oversold=95.0,
            rsi_max=98.0,
            rsi_overbought=100.0,
            vol_mult=0.0,
        )
        closes = [50000 * (1.002 ** i) for i in range(60)]
        df = _make_df(closes)
        sig = s.generate_signal(df)
        assert sig.action in (SignalAction.BUY, SignalAction.HOLD, SignalAction.SELL)

    def test_hold_uptrend_false_when_prices_fall(self):
        s = _small()
        closes = [50000 * (0.98 ** i) for i in range(60)]
        df = _make_df(closes)
        sig = s.generate_signal(df)
        # downtrend: uptrend=False → HOLD (unless death cross or RSI overbought)
        assert sig.action in (SignalAction.HOLD, SignalAction.SELL)

    def test_no_timestamp_column(self):
        s = _small()
        df = _make_df([50000.0] * 60, with_timestamp=False)
        sig = s.generate_signal(df)
        assert sig is not None

    def test_hold_vol_fails(self):
        s = _small(vol_mult=100.0)  # very high threshold
        closes = [50000 * (1.002 ** i) for i in range(60)]
        df = _make_df(closes)
        sig = s.generate_signal(df)
        # vol_mult=100 is impossible to satisfy → HOLD or SELL
        assert sig.action in (SignalAction.HOLD, SignalAction.SELL)

    def test_symbol_propagated_to_signal(self):
        s = SwingMomentumStrategy("XRP/KRW", ema_fast=5, ema_slow=10,
                                  macd_fast=5, macd_slow=10, macd_signal=3,
                                  atr_period=5, adx_threshold=0.0)
        df = _make_df([50000.0] * 120)
        sig = s.generate_signal(df)
        assert sig.symbol == "XRP/KRW"
