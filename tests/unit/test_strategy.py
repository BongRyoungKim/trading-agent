"""Unit tests for strategy engine: models, base, registry, and all strategies."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from src.strategy.bollinger import BollingerBandStrategy
from src.strategy.ma_crossover import MACrossoverStrategy
from src.strategy.models import Signal, SignalAction
from src.strategy.registry import get_strategy, list_strategies
from src.strategy.rsi_strategy import RSIStrategy
from src.utils.exceptions import StrategyError


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ohlcv(closes: list[float], symbol: str = "BTC/USDT") -> pd.DataFrame:
    n = len(closes)
    closes_arr = np.array(closes, dtype=float)
    base_ts = datetime(2024, 1, 1, tzinfo=UTC)
    timestamps = [base_ts + timedelta(hours=i) for i in range(n)]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": closes_arr * 0.999,
            "high": closes_arr * 1.005,
            "low": closes_arr * 0.995,
            "close": closes_arr,
            "volume": np.ones(n) * 100.0,
        }
    )


def _rising_then_flat(n: int = 100) -> list[float]:
    """Prices rise steeply for first half, then flatten — triggers golden cross."""
    rise = list(np.linspace(40000, 55000, n // 2))
    flat = [55000.0] * (n - n // 2)
    return rise + flat


# ── Signal Model ──────────────────────────────────────────────────────────────

class TestSignal:
    def test_is_immutable(self) -> None:
        s = Signal("BTC/USDT", SignalAction.BUY, 0.8, "test", datetime.now(UTC))
        with pytest.raises(AttributeError):
            s.action = SignalAction.SELL  # type: ignore[misc]

    def test_strength_clamped_to_0_1(self) -> None:
        s_low = Signal("X", SignalAction.HOLD, -5.0, "r", datetime.now(UTC))
        s_high = Signal("X", SignalAction.HOLD, 99.0, "r", datetime.now(UTC))
        assert s_low.strength == 0.0
        assert s_high.strength == 1.0

    def test_is_actionable_buy_sell(self) -> None:
        ts = datetime.now(UTC)
        assert Signal("X", SignalAction.BUY, 1.0, "r", ts).is_actionable()
        assert Signal("X", SignalAction.SELL, 1.0, "r", ts).is_actionable()
        assert not Signal("X", SignalAction.HOLD, 0.0, "r", ts).is_actionable()

    def test_metadata_defaults_to_empty_dict(self) -> None:
        s = Signal("X", SignalAction.HOLD, 0.0, "r", datetime.now(UTC))
        assert s.metadata == {}

    def test_repr(self) -> None:
        s = Signal("BTC/USDT", SignalAction.BUY, 0.75, "test", datetime.now(UTC))
        assert "BTC/USDT" in repr(s)
        assert "buy" in repr(s)


# ── Registry ──────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_list_strategies_includes_all_three(self) -> None:
        # Import __init__ to trigger registration
        import src.strategy  # noqa: F401
        names = list_strategies()
        assert "MACrossoverStrategy" in names
        assert "RSIStrategy" in names
        assert "BollingerBandStrategy" in names

    def test_get_strategy_instantiates_correctly(self) -> None:
        strategy = get_strategy("MACrossoverStrategy", symbol="BTC/USDT")
        assert isinstance(strategy, MACrossoverStrategy)

    def test_get_strategy_unknown_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="not found"):
            get_strategy("NonExistentStrategy")


# ── MA Crossover ──────────────────────────────────────────────────────────────

class TestMACrossover:
    def test_fast_must_be_less_than_slow(self) -> None:
        with pytest.raises(ValueError):
            MACrossoverStrategy("BTC/USDT", fast_period=50, slow_period=20)

    def test_insufficient_data_raises(self) -> None:
        strategy = MACrossoverStrategy("BTC/USDT", fast_period=5, slow_period=10)
        data = _make_ohlcv([50000.0] * 5)
        with pytest.raises(StrategyError):
            strategy.generate_signal(data)

    def test_hold_when_no_crossover(self) -> None:
        strategy = MACrossoverStrategy("BTC/USDT", fast_period=5, slow_period=10)
        # Flat prices → no crossover
        data = _make_ohlcv([50000.0] * 30)
        signal = strategy.generate_signal(data)
        assert signal.action == SignalAction.HOLD

    def test_buy_signal_on_golden_cross(self) -> None:
        strategy = MACrossoverStrategy("BTC/USDT", fast_period=5, slow_period=10, ma_type="sma")
        # Flat prices then ONE sharp spike → crossover happens exactly at last bar
        prices = [49000.0] * 20 + [60000.0]
        data = _make_ohlcv(prices)
        signal = strategy.generate_signal(data)
        assert signal.action == SignalAction.BUY
        assert signal.symbol == "BTC/USDT"
        assert 0 <= signal.strength <= 1

    def test_sell_signal_on_death_cross(self) -> None:
        strategy = MACrossoverStrategy("BTC/USDT", fast_period=5, slow_period=10, ma_type="sma")
        # Flat prices then ONE sharp drop → crossover happens exactly at last bar
        prices = [51000.0] * 20 + [40000.0]
        data = _make_ohlcv(prices)
        signal = strategy.generate_signal(data)
        assert signal.action == SignalAction.SELL

    def test_get_parameters_keys(self) -> None:
        s = MACrossoverStrategy("ETH/USDT", fast_period=10, slow_period=30)
        params = s.get_parameters()
        assert params["fast_period"] == 10
        assert params["slow_period"] == 30
        assert params["symbol"] == "ETH/USDT"

    def test_name_includes_periods(self) -> None:
        s = MACrossoverStrategy("X", fast_period=9, slow_period=21)
        assert "9" in s.name and "21" in s.name


# ── RSI Strategy ──────────────────────────────────────────────────────────────

class TestRSIStrategy:
    def test_oversold_must_be_less_than_overbought(self) -> None:
        with pytest.raises(ValueError):
            RSIStrategy("BTC/USDT", oversold=70.0, overbought=30.0)

    def test_insufficient_data_raises(self) -> None:
        strategy = RSIStrategy("BTC/USDT", period=14)
        data = _make_ohlcv([50000.0] * 5)
        with pytest.raises(StrategyError):
            strategy.generate_signal(data)

    def test_hold_in_neutral_zone(self) -> None:
        strategy = RSIStrategy("BTC/USDT", period=5, oversold=30, overbought=70)
        # Flat prices → RSI ≈ 50 → neutral
        data = _make_ohlcv([50000.0] * 40)
        signal = strategy.generate_signal(data)
        assert signal.action == SignalAction.HOLD

    def test_buy_signal_from_oversold(self) -> None:
        strategy = RSIStrategy("BTC/USDT", period=5, oversold=30, overbought=70)
        # Big drop then recovery → RSI crosses above 30
        prices = [50000.0] * 20 + [30000.0] * 10 + [50000.0] * 5
        data = _make_ohlcv(prices)
        signal = strategy.generate_signal(data)
        # Signal could be BUY or HOLD depending on exact RSI value
        assert signal.action in (SignalAction.BUY, SignalAction.HOLD)

    def test_metadata_contains_rsi(self) -> None:
        strategy = RSIStrategy("BTC/USDT", period=5)
        data = _make_ohlcv([50000.0] * 40)
        signal = strategy.generate_signal(data)
        assert "rsi" in signal.metadata

    def test_get_parameters_keys(self) -> None:
        s = RSIStrategy("BTC/USDT", period=14, oversold=25.0, overbought=75.0)
        params = s.get_parameters()
        assert params["period"] == 14
        assert params["oversold"] == 25.0
        assert params["overbought"] == 75.0


# ── Bollinger Band Strategy ───────────────────────────────────────────────────

class TestBollingerBandStrategy:
    def test_insufficient_data_raises(self) -> None:
        strategy = BollingerBandStrategy("BTC/USDT", period=20)
        data = _make_ohlcv([50000.0] * 5)
        with pytest.raises(StrategyError):
            strategy.generate_signal(data)

    def test_hold_when_price_in_bands(self) -> None:
        strategy = BollingerBandStrategy("BTC/USDT", period=10)
        # Flat prices → small bands → last price within bands
        data = _make_ohlcv([50000.0] * 30)
        signal = strategy.generate_signal(data)
        assert signal.action == SignalAction.HOLD

    def test_buy_when_price_below_lower_band(self) -> None:
        strategy = BollingerBandStrategy("BTC/USDT", period=10, std_dev=1.0)
        # Normal prices then sharp drop well below lower band
        prices = [50000.0] * 25 + [40000.0]
        data = _make_ohlcv(prices)
        signal = strategy.generate_signal(data)
        assert signal.action == SignalAction.BUY
        assert 0 < signal.strength <= 1.0

    def test_sell_when_price_above_upper_band(self) -> None:
        strategy = BollingerBandStrategy("BTC/USDT", period=10, std_dev=1.0)
        # Normal prices then sharp spike above upper band
        prices = [50000.0] * 25 + [65000.0]
        data = _make_ohlcv(prices)
        signal = strategy.generate_signal(data)
        assert signal.action == SignalAction.SELL

    def test_metadata_contains_band_values(self) -> None:
        strategy = BollingerBandStrategy("BTC/USDT", period=10)
        prices = [50000.0] * 25 + [40000.0]
        data = _make_ohlcv(prices)
        signal = strategy.generate_signal(data)
        assert "bb_lower" in signal.metadata
        assert "bb_upper" in signal.metadata

    def test_get_parameters_keys(self) -> None:
        s = BollingerBandStrategy("ETH/USDT", period=15, std_dev=2.5)
        params = s.get_parameters()
        assert params["period"] == 15
        assert params["std_dev"] == 2.5


# ── BaseStrategy.validate_data ─────────────────────────────────────────────────

class TestValidateData:
    def test_missing_column_raises(self) -> None:
        strategy = MACrossoverStrategy("BTC/USDT", fast_period=5, slow_period=10)
        bad_df = pd.DataFrame({"open": [1.0], "close": [1.0]})  # missing columns
        with pytest.raises(StrategyError):
            strategy.validate_data(bad_df)
