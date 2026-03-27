"""Unit tests for risk management: position sizing and RiskManager."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.risk.manager import PortfolioState, RiskManager
from src.risk.position_sizing import fixed_fraction, kelly_criterion, percent_of_equity
from src.utils.exceptions import MaxDrawdownExceededError, PositionLimitExceededError


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_settings(**overrides):
    from unittest.mock import MagicMock
    s = MagicMock()
    s.max_drawdown_halt = overrides.get("max_drawdown_halt", 0.15)
    s.max_daily_loss = overrides.get("max_daily_loss", 0.05)
    s.max_open_positions = overrides.get("max_open_positions", 5)
    s.max_position_risk = overrides.get("max_position_risk", 0.02)
    return s


def _make_portfolio(capital: float = 10000.0) -> PortfolioState:
    cap = Decimal(str(capital))
    return PortfolioState(capital=cap, peak_capital=cap)


# ── Position Sizing ───────────────────────────────────────────────────────────

class TestFixedFraction:
    def test_basic_calculation(self) -> None:
        size = fixed_fraction(
            capital=Decimal("10000"),
            risk_fraction=0.02,
            entry_price=Decimal("50000"),
            stop_loss_price=Decimal("48000"),
        )
        # risk_amount = 200, price_risk = 2000 → size = 0.1
        assert abs(float(size) - 0.1) < 1e-8

    def test_zero_price_risk_returns_zero(self) -> None:
        size = fixed_fraction(
            capital=Decimal("10000"),
            risk_fraction=0.02,
            entry_price=Decimal("50000"),
            stop_loss_price=Decimal("50000"),  # same as entry
        )
        assert size == Decimal("0")

    def test_larger_risk_gives_larger_size(self) -> None:
        s1 = fixed_fraction(Decimal("10000"), 0.01, Decimal("50000"), Decimal("48000"))
        s2 = fixed_fraction(Decimal("10000"), 0.02, Decimal("50000"), Decimal("48000"))
        assert s2 > s1


class TestKellyCriterion:
    def test_positive_edge(self) -> None:
        # 60% win rate, avg win = avg loss → Kelly = 0.2
        f = kelly_criterion(win_rate=0.6, avg_win=0.1, avg_loss=0.1)
        assert abs(f - 0.2) < 0.01

    def test_negative_edge_returns_zero(self) -> None:
        # 40% win rate, equal win/loss → negative Kelly → clamped to 0
        f = kelly_criterion(win_rate=0.4, avg_win=0.1, avg_loss=0.1)
        assert f == 0.0

    def test_capped_at_max_fraction(self) -> None:
        # Very high Kelly (e.g., 90% win rate) should be capped
        f = kelly_criterion(win_rate=0.9, avg_win=0.5, avg_loss=0.1, max_fraction=0.25)
        assert f <= 0.25

    def test_zero_avg_loss_returns_zero(self) -> None:
        assert kelly_criterion(0.6, 0.1, 0.0) == 0.0


class TestPercentOfEquity:
    def test_basic_calculation(self) -> None:
        size = percent_of_equity(
            capital=Decimal("10000"),
            pct=0.10,
            entry_price=Decimal("50000"),
        )
        # 10% of 10000 = 1000 / 50000 = 0.02
        assert abs(float(size) - 0.02) < 1e-8

    def test_zero_price_returns_zero(self) -> None:
        size = percent_of_equity(Decimal("10000"), 0.1, Decimal("0"))
        assert size == Decimal("0")


# ── PortfolioState ────────────────────────────────────────────────────────────

class TestPortfolioState:
    def test_drawdown_zero_at_peak(self) -> None:
        state = _make_portfolio(10000.0)
        assert state.current_drawdown_pct == 0.0

    def test_drawdown_calculated_correctly(self) -> None:
        state = PortfolioState(
            capital=Decimal("8500"),
            peak_capital=Decimal("10000"),
        )
        assert abs(state.current_drawdown_pct - 0.15) < 1e-6

    def test_update_peak_on_new_high(self) -> None:
        state = _make_portfolio(10000.0)
        state.capital = Decimal("12000")
        state.update_peak()
        assert state.peak_capital == Decimal("12000")

    def test_record_trade_win_increases_capital(self) -> None:
        state = _make_portfolio(10000.0)
        state.record_trade_result(Decimal("500"))
        assert state.capital == Decimal("10500")
        assert state.daily_loss == Decimal("0")

    def test_record_trade_loss_increases_daily_loss(self) -> None:
        state = _make_portfolio(10000.0)
        state.record_trade_result(Decimal("-200"))
        assert state.capital == Decimal("9800")
        assert state.daily_loss == Decimal("200")


# ── RiskManager ───────────────────────────────────────────────────────────────

class TestRiskManagerDrawdown:
    def test_passes_when_below_limit(self) -> None:
        settings = _make_settings(max_drawdown_halt=0.20)
        state = PortfolioState(
            capital=Decimal("8500"), peak_capital=Decimal("10000")
        )
        rm = RiskManager(settings, state)
        rm.check_drawdown()  # 15% < 20% → should not raise

    def test_raises_when_at_limit(self) -> None:
        settings = _make_settings(max_drawdown_halt=0.15)
        state = PortfolioState(
            capital=Decimal("8500"), peak_capital=Decimal("10000")
        )
        rm = RiskManager(settings, state)
        with pytest.raises(MaxDrawdownExceededError):
            rm.check_drawdown()

    def test_raises_when_above_limit(self) -> None:
        settings = _make_settings(max_drawdown_halt=0.10)
        state = PortfolioState(
            capital=Decimal("8000"), peak_capital=Decimal("10000")
        )
        rm = RiskManager(settings, state)
        with pytest.raises(MaxDrawdownExceededError):
            rm.check_drawdown()


class TestRiskManagerPositionLimit:
    def test_passes_when_below_limit(self) -> None:
        settings = _make_settings(max_open_positions=5)
        state = _make_portfolio()
        state.open_positions = 4
        rm = RiskManager(settings, state)
        rm.check_position_limit()  # 4 < 5 → should not raise

    def test_raises_when_at_limit(self) -> None:
        settings = _make_settings(max_open_positions=3)
        state = _make_portfolio()
        state.open_positions = 3
        rm = RiskManager(settings, state)
        with pytest.raises(PositionLimitExceededError):
            rm.check_position_limit()


class TestRiskManagerDailyLoss:
    def test_passes_below_daily_limit(self) -> None:
        settings = _make_settings(max_daily_loss=0.05)
        state = _make_portfolio(10000.0)
        state.daily_loss = Decimal("300")  # 3% < 5%
        rm = RiskManager(settings, state)
        rm.check_daily_loss()  # should not raise

    def test_raises_at_daily_limit(self) -> None:
        settings = _make_settings(max_daily_loss=0.05)
        state = _make_portfolio(10000.0)
        state.daily_loss = Decimal("500")  # 5% == limit
        rm = RiskManager(settings, state)
        with pytest.raises(MaxDrawdownExceededError):
            rm.check_daily_loss()


class TestRiskManagerStopLoss:
    def test_atr_based_stop_loss_buy(self) -> None:
        settings = _make_settings()
        state = _make_portfolio()
        rm = RiskManager(settings, state)
        stop = rm.calculate_stop_loss(
            Decimal("50000"), side="buy", atr_value=500.0, atr_multiplier=2.0
        )
        assert stop == Decimal("49000")  # 50000 - 500*2

    def test_fixed_pct_stop_loss_buy(self) -> None:
        settings = _make_settings()
        state = _make_portfolio()
        rm = RiskManager(settings, state)
        stop = rm.calculate_stop_loss(
            Decimal("50000"), side="buy", fixed_pct=0.02
        )
        assert stop == Decimal("49000")  # 50000 * (1 - 0.02)

    def test_short_stop_loss_above_entry(self) -> None:
        settings = _make_settings()
        state = _make_portfolio()
        rm = RiskManager(settings, state)
        stop = rm.calculate_stop_loss(
            Decimal("50000"), side="sell", fixed_pct=0.02
        )
        assert stop == Decimal("51000")  # 50000 * (1 + 0.02)

    def test_default_stop_uses_max_position_risk(self) -> None:
        settings = _make_settings(max_position_risk=0.02)
        state = _make_portfolio()
        rm = RiskManager(settings, state)
        stop = rm.calculate_stop_loss(Decimal("50000"), side="buy")
        assert stop == Decimal("49000")  # 50000 - 50000*0.02


class TestRiskManagerCallbacks:
    def test_on_position_opened_increments_count(self) -> None:
        settings = _make_settings()
        state = _make_portfolio()
        rm = RiskManager(settings, state)
        rm.on_position_opened()
        assert state.open_positions == 1

    def test_on_position_closed_decrements_and_updates_capital(self) -> None:
        settings = _make_settings()
        state = _make_portfolio(10000.0)
        state.open_positions = 1
        rm = RiskManager(settings, state)
        rm.on_position_closed(Decimal("300"))
        assert state.open_positions == 0
        assert state.capital == Decimal("10300")

    def test_on_position_closed_never_goes_below_zero_positions(self) -> None:
        settings = _make_settings()
        state = _make_portfolio()
        state.open_positions = 0
        rm = RiskManager(settings, state)
        rm.on_position_closed(Decimal("0"))
        assert state.open_positions == 0
