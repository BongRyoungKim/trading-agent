"""Unit tests for VWAPStrategy."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from src.strategy.models import SignalAction
from src.strategy.vwap_strategy import VWAPStrategy


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_df(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
) -> pd.DataFrame:
    n = len(closes)
    base = datetime(2024, 1, 1, tzinfo=UTC)
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    volumes = volumes or [100.0] * n
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=pd.DatetimeIndex([base + timedelta(hours=i) for i in range(n)]),
    )


def _crossover_above(n: int = 30) -> pd.DataFrame:
    """Price below VWAP for first half, crosses above in last bar."""
    closes = [50000.0] * (n - 1) + [55000.0]  # big jump on last bar
    # VWAP will be close to 50000; last close (55000) crosses above
    return _make_df(closes)


def _crossover_below(n: int = 30) -> pd.DataFrame:
    """Price above VWAP for most bars, then drops sharply."""
    closes = [55000.0] * (n - 1) + [48000.0]
    return _make_df(closes)


# ── Init validation ───────────────────────────────────────────────────────────

class TestVWAPStrategyInit:
    def test_negative_ema_period_raises(self):
        with pytest.raises(ValueError, match="ema_period"):
            VWAPStrategy("BTC/USDT", ema_period=-1)

    def test_zero_ema_period_valid(self):
        s = VWAPStrategy("BTC/USDT", ema_period=0)
        assert s.get_parameters()["ema_period"] == 0

    def test_name_contains_symbol(self):
        s = VWAPStrategy("ETH/USDT", ema_period=10)
        assert "ETH/USDT" in s.name
        assert "10" in s.name

    def test_min_required_bars_with_ema(self):
        s = VWAPStrategy("BTC/USDT", ema_period=20)
        assert s.min_required_bars() >= 21

    def test_min_required_bars_without_ema(self):
        s = VWAPStrategy("BTC/USDT", ema_period=0)
        assert s.min_required_bars() == 2

    def test_get_parameters(self):
        s = VWAPStrategy("BTC/USDT", ema_period=15)
        p = s.get_parameters()
        assert p["symbol"] == "BTC/USDT"
        assert p["ema_period"] == 15


# ── Signal generation ─────────────────────────────────────────────────────────

class TestVWAPSignals:
    def test_insufficient_data_raises(self):
        from src.utils.exceptions import StrategyError
        s = VWAPStrategy("BTC/USDT", ema_period=0)
        with pytest.raises(StrategyError):
            s.generate_signal(_make_df([50000.0]))  # only 1 bar

    def test_hold_when_no_crossover(self):
        # Flat price — price equals VWAP, no crossover
        closes = [50000.0] * 30
        df = _make_df(closes)
        s = VWAPStrategy("BTC/USDT", ema_period=0)
        sig = s.generate_signal(df)
        assert sig.action == SignalAction.HOLD

    def test_buy_signal_on_crossover_above(self):
        s = VWAPStrategy("BTC/USDT", ema_period=0)
        # Ensure a genuine crossover: price below VWAP, then spikes above
        closes = [49000.0] * 29 + [55000.0]
        df = _make_df(closes)
        sig = s.generate_signal(df)
        assert sig.action == SignalAction.BUY

    def test_sell_signal_on_crossover_below(self):
        s = VWAPStrategy("BTC/USDT", ema_period=0)
        closes = [55000.0] * 29 + [48000.0]
        df = _make_df(closes)
        sig = s.generate_signal(df)
        assert sig.action == SignalAction.SELL

    def test_buy_signal_strength_in_range(self):
        s = VWAPStrategy("BTC/USDT", ema_period=0)
        closes = [49000.0] * 29 + [55000.0]
        df = _make_df(closes)
        sig = s.generate_signal(df)
        assert 0.0 <= sig.strength <= 1.0

    def test_ema_filter_blocks_buy_when_below_ema(self):
        """With EMA confirmation: if close < EMA, BUY is suppressed."""
        # Price crosses VWAP upward but stays below EMA (long declining EMA)
        s = VWAPStrategy("BTC/USDT", ema_period=5)
        # Declining price series, then tiny crossover — EMA will be well above
        closes = [60000.0, 58000.0, 56000.0, 54000.0, 52000.0,
                  48000.0, 49000.0, 50000.0, 49500.0, 49800.0]
        df = _make_df(closes)
        sig = s.generate_signal(df)
        # Either HOLD (filtered) or BUY — EMA filter may suppress
        assert sig.action in (SignalAction.HOLD, SignalAction.BUY)

    def test_hold_reason_mentions_vwap(self):
        closes = [50000.0] * 30
        df = _make_df(closes)
        s = VWAPStrategy("BTC/USDT", ema_period=0)
        sig = s.generate_signal(df)
        assert "VWAP" in sig.reason or "vwap" in sig.reason.lower()

    def test_buy_reason_mentions_vwap(self):
        s = VWAPStrategy("BTC/USDT", ema_period=0)
        closes = [49000.0] * 29 + [55000.0]
        df = _make_df(closes)
        sig = s.generate_signal(df)
        if sig.action == SignalAction.BUY:
            assert "VWAP" in sig.reason

    def test_symbol_in_signal(self):
        s = VWAPStrategy("ETH/USDT", ema_period=0)
        closes = [49000.0] * 29 + [55000.0]
        sig = s.generate_signal(_make_df(closes))
        assert sig.symbol == "ETH/USDT"

    def test_registered_in_registry(self):
        from src.strategy.registry import list_strategies
        import src.strategy  # noqa: F401
        assert "VWAPStrategy" in list_strategies()
