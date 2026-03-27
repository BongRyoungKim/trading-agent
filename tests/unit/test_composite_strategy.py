"""Unit tests for CompositeStrategy."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.strategy.composite import CompositeStrategy
from src.strategy.models import Signal, SignalAction


# ── Helpers ───────────────────────────────────────────────────────────────────

def _signal(action: SignalAction, strength: float = 0.8) -> Signal:
    return Signal(
        symbol="BTC/USDT",
        action=action,
        strength=strength,
        reason="test",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _mock_strategy(action: SignalAction, strength: float = 0.8) -> MagicMock:
    strat = MagicMock()
    strat.generate_signal.return_value = _signal(action, strength)
    strat.min_required_bars.return_value = 1
    strat.name = f"mock_{action.value}"
    return strat


def _make_df(n: int = 50) -> pd.DataFrame:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return pd.DataFrame(
        {
            "open": [50000.0] * n,
            "high": [51000.0] * n,
            "low": [49000.0] * n,
            "close": [50000.0] * n,
            "volume": [100.0] * n,
        },
        index=pd.DatetimeIndex([base + timedelta(hours=i) for i in range(n)]),
    )


# ── Init validation ───────────────────────────────────────────────────────────

class TestCompositeStrategyInit:
    def test_empty_strategies_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            CompositeStrategy(symbol="BTC/USDT", strategies=[])

    def test_invalid_mode_raises(self):
        strat = _mock_strategy(SignalAction.HOLD)
        with pytest.raises(ValueError, match="mode"):
            CompositeStrategy(symbol="BTC/USDT", strategies=[strat], mode="invalid")  # type: ignore

    def test_name_contains_mode_and_sub_names(self):
        s1 = _mock_strategy(SignalAction.BUY)
        s1.name = "ma"
        s2 = _mock_strategy(SignalAction.BUY)
        s2.name = "rsi"
        comp = CompositeStrategy("BTC/USDT", [s1, s2], mode="all")
        assert "all" in comp.name
        assert "ma" in comp.name
        assert "rsi" in comp.name

    def test_min_required_bars_is_max_of_sub_strategies(self):
        s1 = _mock_strategy(SignalAction.HOLD)
        s1.min_required_bars.return_value = 20
        s2 = _mock_strategy(SignalAction.HOLD)
        s2.min_required_bars.return_value = 50
        comp = CompositeStrategy("BTC/USDT", [s1, s2])
        assert comp.min_required_bars() == 50

    def test_get_parameters_includes_sub_strategies(self):
        s1 = _mock_strategy(SignalAction.HOLD)
        s1.name = "ma"
        comp = CompositeStrategy("BTC/USDT", [s1], mode="any")
        params = comp.get_parameters()
        assert params["mode"] == "any"
        assert "ma" in params["sub_strategies"]


# ── "all" mode ────────────────────────────────────────────────────────────────

class TestCompositeModeAll:
    def _make(self, *actions: SignalAction) -> CompositeStrategy:
        return CompositeStrategy(
            symbol="BTC/USDT",
            strategies=[_mock_strategy(a) for a in actions],
            mode="all",
        )

    def test_all_buy_returns_buy(self):
        comp = self._make(SignalAction.BUY, SignalAction.BUY)
        sig = comp.generate_signal(_make_df())
        assert sig.action == SignalAction.BUY

    def test_all_sell_returns_sell(self):
        comp = self._make(SignalAction.SELL, SignalAction.SELL)
        sig = comp.generate_signal(_make_df())
        assert sig.action == SignalAction.SELL

    def test_mixed_buy_sell_returns_hold(self):
        comp = self._make(SignalAction.BUY, SignalAction.SELL)
        sig = comp.generate_signal(_make_df())
        assert sig.action == SignalAction.HOLD

    def test_buy_and_hold_returns_hold(self):
        comp = self._make(SignalAction.BUY, SignalAction.HOLD)
        sig = comp.generate_signal(_make_df())
        assert sig.action == SignalAction.HOLD

    def test_single_strategy_buy(self):
        comp = self._make(SignalAction.BUY)
        sig = comp.generate_signal(_make_df())
        assert sig.action == SignalAction.BUY


# ── "any" mode ────────────────────────────────────────────────────────────────

class TestCompositeModeAny:
    def _make(self, *actions: SignalAction) -> CompositeStrategy:
        return CompositeStrategy(
            symbol="BTC/USDT",
            strategies=[_mock_strategy(a) for a in actions],
            mode="any",
        )

    def test_one_buy_among_holds_returns_buy(self):
        comp = self._make(SignalAction.BUY, SignalAction.HOLD, SignalAction.HOLD)
        sig = comp.generate_signal(_make_df())
        assert sig.action == SignalAction.BUY

    def test_all_hold_returns_hold(self):
        comp = self._make(SignalAction.HOLD, SignalAction.HOLD)
        sig = comp.generate_signal(_make_df())
        assert sig.action == SignalAction.HOLD

    def test_buy_takes_priority_over_sell_when_both_present(self):
        # BUY is checked first in generate_signal
        comp = self._make(SignalAction.BUY, SignalAction.SELL)
        sig = comp.generate_signal(_make_df())
        assert sig.action == SignalAction.BUY


# ── "majority" mode ───────────────────────────────────────────────────────────

class TestCompositeModeMajority:
    def _make(self, *actions: SignalAction) -> CompositeStrategy:
        return CompositeStrategy(
            symbol="BTC/USDT",
            strategies=[_mock_strategy(a) for a in actions],
            mode="majority",
        )

    def test_two_out_of_three_buy_returns_buy(self):
        comp = self._make(SignalAction.BUY, SignalAction.BUY, SignalAction.HOLD)
        sig = comp.generate_signal(_make_df())
        assert sig.action == SignalAction.BUY

    def test_one_out_of_three_buy_returns_hold(self):
        comp = self._make(SignalAction.BUY, SignalAction.HOLD, SignalAction.HOLD)
        sig = comp.generate_signal(_make_df())
        assert sig.action == SignalAction.HOLD

    def test_exact_half_does_not_pass_majority(self):
        # 2 out of 4 = 50% → not > 50% → HOLD
        comp = self._make(SignalAction.BUY, SignalAction.BUY, SignalAction.HOLD, SignalAction.HOLD)
        sig = comp.generate_signal(_make_df())
        assert sig.action == SignalAction.HOLD

    def test_three_out_of_four_buy_returns_buy(self):
        comp = self._make(
            SignalAction.BUY, SignalAction.BUY, SignalAction.BUY, SignalAction.HOLD
        )
        sig = comp.generate_signal(_make_df())
        assert sig.action == SignalAction.BUY


# ── Signal properties ─────────────────────────────────────────────────────────

class TestCompositeSignalProperties:
    def test_strength_is_average_of_agreeing(self):
        s1 = _mock_strategy(SignalAction.BUY, strength=0.6)
        s2 = _mock_strategy(SignalAction.BUY, strength=0.8)
        comp = CompositeStrategy("BTC/USDT", [s1, s2], mode="all")
        sig = comp.generate_signal(_make_df())
        assert abs(sig.strength - 0.7) < 1e-9

    def test_reason_contains_vote_info(self):
        s1 = _mock_strategy(SignalAction.BUY)
        s2 = _mock_strategy(SignalAction.BUY)
        comp = CompositeStrategy("BTC/USDT", [s1, s2], mode="all")
        sig = comp.generate_signal(_make_df())
        assert "2/2" in sig.reason

    def test_sub_strategy_error_treated_as_hold(self):
        from src.utils.exceptions import StrategyError
        s1 = MagicMock()
        s1.generate_signal.side_effect = StrategyError("not enough bars")
        s1.min_required_bars.return_value = 1
        s1.name = "erroring"
        s2 = _mock_strategy(SignalAction.BUY)
        # "all" mode: one HOLD (error) + one BUY → HOLD
        comp = CompositeStrategy("BTC/USDT", [s1, s2], mode="all")
        sig = comp.generate_signal(_make_df())
        assert sig.action == SignalAction.HOLD
