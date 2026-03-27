"""Unit tests for RSIMomentumStrategy (Ross Cameron RSI Momentum)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

from src.strategy.models import SignalAction
from src.strategy.rsi_momentum import RSIMomentumStrategy


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_df(
    n: int = 50,
    price: float = 50000.0,
    volumes: list[float] | None = None,
) -> pd.DataFrame:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    closes = [price] * n
    vols = volumes if volumes is not None else [1000.0] * n
    return pd.DataFrame(
        {
            "timestamp": [base + timedelta(hours=i) for i in range(n)],
            "open": closes,
            "high": [c * 1.005 for c in closes],
            "low": [c * 0.995 for c in closes],
            "close": closes,
            "volume": vols,
        }
    )


def _rsi_series(values: list[float], n: int = 50) -> pd.Series:
    """Return a pd.Series of length n where the last len(values) items are given."""
    prefix = [50.0] * (n - len(values))
    return pd.Series(prefix + list(values))


def _low_avg_high_vol_df(n: int = 50, avg_vol: float = 1000.0, surge_mult: float = 2.0) -> pd.DataFrame:
    """DataFrame where the last bar has a volume surge."""
    vols = [avg_vol] * (n - 1) + [avg_vol * surge_mult]
    return _make_df(n=n, volumes=vols)


def _flat_vol_df(n: int = 50, vol: float = 1000.0) -> pd.DataFrame:
    return _make_df(n=n, volumes=[vol] * n)


# ── Init validation ───────────────────────────────────────────────────────────

class TestRSIMomentumInit:
    def test_valid_defaults(self) -> None:
        s = RSIMomentumStrategy("BTC/USDT")
        assert s.name == "rsi_momentum_14_BTC/USDT"

    def test_rsi_period_too_small_raises(self) -> None:
        with pytest.raises(ValueError, match="rsi_period"):
            RSIMomentumStrategy("BTC/USDT", rsi_period=1)

    def test_momentum_threshold_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="momentum_threshold"):
            RSIMomentumStrategy("BTC/USDT", momentum_threshold=0.0)

    def test_overbought_le_threshold_raises(self) -> None:
        with pytest.raises(ValueError, match="overbought"):
            RSIMomentumStrategy("BTC/USDT", momentum_threshold=70.0, overbought=50.0)

    def test_volume_mult_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="volume_mult"):
            RSIMomentumStrategy("BTC/USDT", volume_mult=0.0)

    def test_volume_lookback_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="volume_lookback"):
            RSIMomentumStrategy("BTC/USDT", volume_lookback=0)

    def test_get_parameters_returns_all_fields(self) -> None:
        s = RSIMomentumStrategy(
            "ETH/USDT",
            rsi_period=9,
            momentum_threshold=55.0,
            overbought=80.0,
            volume_mult=2.0,
            volume_lookback=10,
        )
        params = s.get_parameters()
        assert params["rsi_period"] == 9
        assert params["momentum_threshold"] == 55.0
        assert params["overbought"] == 80.0
        assert params["volume_mult"] == 2.0
        assert params["volume_lookback"] == 10

    def test_min_required_bars_uses_max_of_rsi_and_volume(self) -> None:
        # rsi_period=14 → 28, volume_lookback=20 → 21 → max=28
        s = RSIMomentumStrategy("BTC/USDT", rsi_period=14, volume_lookback=20)
        assert s.min_required_bars() == 28

    def test_min_required_bars_volume_dominates(self) -> None:
        # rsi_period=5 → 10, volume_lookback=50 → 51 → max=51
        s = RSIMomentumStrategy("BTC/USDT", rsi_period=5, volume_lookback=50)
        assert s.min_required_bars() == 51


# ── Registry ──────────────────────────────────────────────────────────────────

class TestRegistration:
    def test_registered_in_registry(self) -> None:
        from src.strategy.registry import get_strategy
        import src.strategy  # noqa: F401
        s = get_strategy("RSIMomentumStrategy", symbol="BTC/USDT")
        assert isinstance(s, RSIMomentumStrategy)


# ── Signal logic ──────────────────────────────────────────────────────────────

class TestBuySignal:
    def test_buy_signal_rsi_cross_above_50_with_volume_surge(self) -> None:
        """RSI crosses above 50 + volume surge → BUY."""
        df = _low_avg_high_vol_df(n=50, avg_vol=1000.0, surge_mult=2.0)
        strategy = RSIMomentumStrategy("BTC/USDT", volume_mult=1.5, volume_lookback=20)

        rsi_vals = _rsi_series([45.0, 52.0])  # prev<50, now>50
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=rsi_vals):
            signal = strategy.generate_signal(df)

        assert signal.action == SignalAction.BUY
        assert signal.strength > 0
        assert "crossed" in signal.reason
        assert "volume surge" in signal.reason
        assert "rsi" in signal.metadata

    def test_buy_signal_strength_increases_with_rsi_distance(self) -> None:
        """Higher RSI above threshold → higher strength."""
        df = _low_avg_high_vol_df(n=50, avg_vol=1000.0, surge_mult=3.0)
        strategy = RSIMomentumStrategy(
            "BTC/USDT", momentum_threshold=50.0, overbought=75.0, volume_mult=1.5, volume_lookback=20
        )

        # RSI barely crosses (50.1) → low strength
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=_rsi_series([48.0, 50.1])):
            signal_low = strategy.generate_signal(df)

        # RSI well above (70) → high strength
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=_rsi_series([48.0, 70.0])):
            signal_high = strategy.generate_signal(df)

        assert signal_low.action == SignalAction.BUY
        assert signal_high.action == SignalAction.BUY
        assert signal_high.strength >= signal_low.strength

    def test_buy_blocked_no_volume_surge(self) -> None:
        """RSI crosses 50 but volume is flat → HOLD."""
        df = _flat_vol_df(n=50, vol=1000.0)
        strategy = RSIMomentumStrategy("BTC/USDT", volume_mult=1.5, volume_lookback=20)

        rsi_vals = _rsi_series([48.0, 52.0])  # RSI crosses
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=rsi_vals):
            signal = strategy.generate_signal(df)

        assert signal.action == SignalAction.HOLD
        assert "no volume surge" in signal.reason

    def test_buy_blocked_entry_already_overbought(self) -> None:
        """RSI > overbought on both prev and current → block new entry."""
        df = _low_avg_high_vol_df(n=50, avg_vol=1000.0, surge_mult=2.0)
        strategy = RSIMomentumStrategy("BTC/USDT", overbought=75.0, volume_mult=1.5)

        rsi_vals = _rsi_series([78.0, 80.0])  # both above overbought
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=rsi_vals):
            signal = strategy.generate_signal(df)

        assert signal.action == SignalAction.HOLD
        assert "overbought" in signal.reason


class TestSellSignal:
    def test_sell_rsi_drops_below_50_momentum_exhausted(self) -> None:
        """RSI crosses below 50 → SELL (momentum exhausted)."""
        df = _flat_vol_df(n=50)
        strategy = RSIMomentumStrategy("BTC/USDT")

        rsi_vals = _rsi_series([52.0, 48.0])  # was above, now below
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=rsi_vals):
            signal = strategy.generate_signal(df)

        assert signal.action == SignalAction.SELL
        assert "momentum exhausted" in signal.reason
        assert signal.strength == 0.8

    def test_sell_rsi_overbought_profit_taking(self) -> None:
        """RSI >= overbought from below → SELL (profit-taking)."""
        df = _flat_vol_df(n=50)
        strategy = RSIMomentumStrategy("BTC/USDT", overbought=75.0)

        # prev was below overbought, now crosses into overbought
        rsi_vals = _rsi_series([60.0, 76.0])
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=rsi_vals):
            signal = strategy.generate_signal(df)

        assert signal.action == SignalAction.SELL
        assert "overbought" in signal.reason
        assert "profit" in signal.reason
        assert signal.strength == 1.0

    def test_sell_overbought_strength_is_max(self) -> None:
        strategy = RSIMomentumStrategy("BTC/USDT", overbought=75.0)
        df = _flat_vol_df(n=50)

        rsi_vals = _rsi_series([74.0, 77.0])  # enters overbought zone
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=rsi_vals):
            signal = strategy.generate_signal(df)

        assert signal.action == SignalAction.SELL
        assert signal.strength == 1.0


class TestHoldSignal:
    def test_hold_rsi_neutral_no_cross(self) -> None:
        """RSI in neutral zone with no cross → HOLD."""
        df = _flat_vol_df(n=50)
        strategy = RSIMomentumStrategy("BTC/USDT")

        rsi_vals = _rsi_series([55.0, 57.0])  # above 50, no cross, below 75
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=rsi_vals):
            signal = strategy.generate_signal(df)

        assert signal.action == SignalAction.HOLD
        assert signal.strength == 0.0

    def test_hold_rsi_below_50_no_cross(self) -> None:
        """RSI below 50, no cross → HOLD."""
        df = _flat_vol_df(n=50)
        strategy = RSIMomentumStrategy("BTC/USDT")

        rsi_vals = _rsi_series([40.0, 43.0])  # stays below 50
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=rsi_vals):
            signal = strategy.generate_signal(df)

        assert signal.action == SignalAction.HOLD

    def test_hold_metadata_contains_rsi(self) -> None:
        df = _flat_vol_df(n=50)
        strategy = RSIMomentumStrategy("BTC/USDT")

        rsi_vals = _rsi_series([55.0, 57.0])
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=rsi_vals):
            signal = strategy.generate_signal(df)

        assert "rsi" in signal.metadata
        assert signal.metadata["rsi"] == 57.0


class TestEdgeCases:
    def test_insufficient_data_raises(self) -> None:
        strategy = RSIMomentumStrategy("BTC/USDT", rsi_period=14, volume_lookback=20)
        df = _make_df(n=10)
        from src.utils.exceptions import StrategyError
        with pytest.raises(StrategyError):
            strategy.generate_signal(df)

    def test_volume_ratio_in_buy_metadata(self) -> None:
        """BUY signal includes volume_ratio in metadata."""
        df = _low_avg_high_vol_df(n=50, avg_vol=1000.0, surge_mult=2.0)
        strategy = RSIMomentumStrategy("BTC/USDT", volume_mult=1.5, volume_lookback=20)

        rsi_vals = _rsi_series([45.0, 52.0])
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=rsi_vals):
            signal = strategy.generate_signal(df)

        assert signal.action == SignalAction.BUY
        assert "volume_ratio" in signal.metadata
        assert signal.metadata["volume_ratio"] > 1.0

    def test_custom_thresholds(self) -> None:
        """Custom momentum_threshold and overbought values are respected."""
        df = _low_avg_high_vol_df(n=50, avg_vol=500.0, surge_mult=3.0)
        strategy = RSIMomentumStrategy(
            "BTC/USDT",
            momentum_threshold=40.0,
            overbought=65.0,
            volume_mult=2.0,
            volume_lookback=20,
        )
        # RSI crosses above custom threshold of 40
        rsi_vals = _rsi_series([38.0, 42.0])
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=rsi_vals):
            signal = strategy.generate_signal(df)

        assert signal.action == SignalAction.BUY

    def test_symbol_in_signal(self) -> None:
        strategy = RSIMomentumStrategy("ETH/USDT")
        df = _flat_vol_df(n=50)

        rsi_vals = _rsi_series([55.0, 58.0])
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=rsi_vals):
            signal = strategy.generate_signal(df)

        assert signal.symbol == "ETH/USDT"

    def test_timestamp_from_dataframe(self) -> None:
        """Signal timestamp should come from the last row of data."""
        df = _make_df(n=50)
        strategy = RSIMomentumStrategy("BTC/USDT")

        rsi_vals = _rsi_series([55.0, 58.0])
        with patch("src.strategy.rsi_momentum.calc_rsi", return_value=rsi_vals):
            signal = strategy.generate_signal(df)

        expected_ts = df["timestamp"].iloc[-1]
        assert signal.timestamp == expected_ts
