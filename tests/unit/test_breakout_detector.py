"""Unit tests for src/breakout/detector.py (pure price/volume breakout logic)."""
from __future__ import annotations

import pandas as pd
import pytest

from src.breakout.detector import BreakoutConfig, BreakoutSignal, detect_breakout


def _make_df(base_prices: list[float], base_volume: float, last_row: dict) -> pd.DataFrame:
    """Build an OHLCV DataFrame: quiet base candles + one final candle (`last_row`)."""
    rows = []
    for p in base_prices:
        rows.append({"open": p, "high": p, "low": p, "close": p, "volume": base_volume})
    rows.append(last_row)
    return pd.DataFrame(rows)


class TestDetectBreakout:
    def _cfg(self, **overrides) -> BreakoutConfig:
        base = dict(
            base_window_bars=10,
            base_range_max_pct=8.0,
            breakout_margin_pct=1.5,
            vol_mult=5.0,
            max_move_from_base_low_pct=20.0,
            min_quote_volume_krw=0.0,
        )
        base.update(overrides)
        return BreakoutConfig(**base)

    def test_valid_breakout_returns_signal(self) -> None:
        cfg = self._cfg()
        # Quiet base around 100 (within 8% range), then a breakout candle:
        # base_high=101, threshold = 101 * 1.015 = 102.515
        df = _make_df(
            base_prices=[99, 100, 101, 100, 99, 100, 101, 100, 99, 100],
            base_volume=10.0,
            last_row={"open": 101, "high": 105, "low": 101, "close": 103, "volume": 60.0},
        )
        signal = detect_breakout(df, "TEST/KRW", cfg)
        assert signal is not None
        assert isinstance(signal, BreakoutSignal)
        assert signal.symbol == "TEST/KRW"
        assert signal.base_high == 101.0
        assert signal.base_low == 99.0
        assert signal.breakout_price == 103.0
        assert signal.volume_ratio == 6.0  # 60 / 10

    def test_insufficient_history_returns_none(self) -> None:
        cfg = self._cfg(base_window_bars=48)
        df = _make_df(
            base_prices=[100] * 10,
            base_volume=10.0,
            last_row={"open": 100, "high": 110, "low": 100, "close": 108, "volume": 100.0},
        )
        assert detect_breakout(df, "TEST/KRW", cfg) is None

    def test_base_too_volatile_rejected(self) -> None:
        cfg = self._cfg()
        # base range: low=80, high=120 -> (120-80)/80*100 = 50% >> 8% cap
        df = _make_df(
            base_prices=[80, 120, 90, 110, 85, 115, 95, 105, 80, 120],
            base_volume=10.0,
            last_row={"open": 120, "high": 130, "low": 120, "close": 128, "volume": 100.0},
        )
        assert detect_breakout(df, "TEST/KRW", cfg) is None

    def test_no_breakout_margin_rejected(self) -> None:
        cfg = self._cfg()
        # base_high = 101, close only at 101.2 -> below 1.5% margin threshold (102.515)
        df = _make_df(
            base_prices=[99, 100, 101, 100, 99, 100, 101, 100, 99, 100],
            base_volume=10.0,
            last_row={"open": 100, "high": 101.5, "low": 100, "close": 101.2, "volume": 100.0},
        )
        assert detect_breakout(df, "TEST/KRW", cfg) is None

    def test_insufficient_volume_rejected(self) -> None:
        cfg = self._cfg()
        # Breaks out on price but volume only 2x base avg (< vol_mult 5.0)
        df = _make_df(
            base_prices=[99, 100, 101, 100, 99, 100, 101, 100, 99, 100],
            base_volume=10.0,
            last_row={"open": 101, "high": 105, "low": 101, "close": 103, "volume": 20.0},
        )
        assert detect_breakout(df, "TEST/KRW", cfg) is None

    def test_chase_buy_cap_rejected(self) -> None:
        cfg = self._cfg(max_move_from_base_low_pct=20.0)
        # base_low = 99, close = 130 -> move_from_base_low_pct ~= 31% > 20% cap
        # (mirrors the SKR/KRW "already up 150%" chase-buy scenario)
        df = _make_df(
            base_prices=[99, 100, 101, 100, 99, 100, 101, 100, 99, 100],
            base_volume=10.0,
            last_row={"open": 101, "high": 132, "low": 101, "close": 130, "volume": 100.0},
        )
        assert detect_breakout(df, "TEST/KRW", cfg) is None

    def test_zero_base_low_rejected(self) -> None:
        cfg = self._cfg()
        df = _make_df(
            base_prices=[0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            base_volume=10.0,
            last_row={"open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 100.0},
        )
        assert detect_breakout(df, "TEST/KRW", cfg) is None

    def test_zero_base_avg_volume_rejected(self) -> None:
        cfg = self._cfg()
        df = _make_df(
            base_prices=[99, 100, 101, 100, 99, 100, 101, 100, 99, 100],
            base_volume=0.0,
            last_row={"open": 101, "high": 105, "low": 101, "close": 103, "volume": 60.0},
        )
        assert detect_breakout(df, "TEST/KRW", cfg) is None

    def test_default_config_valid(self) -> None:
        # BreakoutConfig() should not raise
        cfg = BreakoutConfig()
        assert cfg.base_window_bars == 48
        assert cfg.breakout_margin_pct == 1.5

    def test_invalid_config_raises(self) -> None:
        with pytest.raises(ValueError):
            BreakoutConfig(base_window_bars=1)
        with pytest.raises(ValueError):
            BreakoutConfig(base_range_max_pct=0)
        with pytest.raises(ValueError):
            BreakoutConfig(vol_mult=0)
