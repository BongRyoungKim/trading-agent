"""Unit tests for src/confluence/htf_regime.py (1d/4h uptrend 판정, 순수 로직)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.confluence.htf_regime import is_uptrend


def _uptrend_df(n: int = 120) -> pd.DataFrame:
    # 꾸준히 우상향하는 종가 -> ADX 높고 EMA20>EMA50이 되도록
    close = pd.Series(np.linspace(100, 200, n))
    high = close * 1.01
    low = close * 0.99
    return pd.DataFrame({"high": high, "low": low, "close": close})


def _flat_df(n: int = 120) -> pd.DataFrame:
    # 노이즈만 있고 방향성 없는 횡보 -> ADX 낮음
    rng = np.random.default_rng(42)
    close = pd.Series(100 + rng.normal(0, 0.3, n).cumsum() * 0.05)
    high = close + 0.5
    low = close - 0.5
    return pd.DataFrame({"high": high, "low": low, "close": close})


def _downtrend_df(n: int = 120) -> pd.DataFrame:
    close = pd.Series(np.linspace(200, 100, n))
    high = close * 1.01
    low = close * 0.99
    return pd.DataFrame({"high": high, "low": low, "close": close})


class TestIsUptrend:
    def test_returns_false_when_insufficient_history(self) -> None:
        df = _uptrend_df(n=10)
        assert is_uptrend(df) is False

    def test_true_for_strong_sustained_uptrend(self) -> None:
        assert is_uptrend(_uptrend_df()) is True

    def test_false_for_downtrend(self) -> None:
        assert is_uptrend(_downtrend_df()) is False

    def test_false_for_flat_ranging_market(self) -> None:
        assert is_uptrend(_flat_df()) is False
