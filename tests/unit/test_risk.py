"""Unit tests for risk management: position sizing and RiskManager."""
from __future__ import annotations

from decimal import Decimal

import pytest

from datetime import UTC, datetime, timedelta

from src.risk.manager import PortfolioState, RiskManager
from src.risk.position_sizing import fixed_fraction, kelly_criterion, percent_of_equity
from src.utils.exceptions import MaxDrawdownExceededError, PositionLimitExceededError, TradingCooldownError


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


class TestPortfolioStateConsecutiveLosses:
    def test_starts_at_zero_no_cooldown(self) -> None:
        state = _make_portfolio()
        assert state.consecutive_losses == 0
        assert state.cooldown_until is None

    def test_loss_increments_counter(self) -> None:
        state = _make_portfolio()
        state.record_trade_result(Decimal("-100"))
        assert state.consecutive_losses == 1
        state.record_trade_result(Decimal("-50"))
        assert state.consecutive_losses == 2

    def test_win_resets_counter(self) -> None:
        state = _make_portfolio()
        state.record_trade_result(Decimal("-100"))
        state.record_trade_result(Decimal("50"))
        assert state.consecutive_losses == 0

    def test_zero_pnl_resets_counter(self) -> None:
        # pnl == 0 is not a loss (matches daily_loss's strict "pnl < 0" rule)
        state = _make_portfolio()
        state.record_trade_result(Decimal("-100"))
        state.record_trade_result(Decimal("0"))
        assert state.consecutive_losses == 0


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


# ── Consecutive-loss circuit breaker ──────────────────────────────────────────
# Constructor-level defaults (not Settings-based) so a bare MagicMock() Settings
# object used elsewhere in the test suite never needs updating — see
# RiskManager.__init__ for rationale.

class TestRiskManagerCircuitBreakerDefaults:
    def test_default_limit_is_three(self) -> None:
        rm = RiskManager(_make_settings(), _make_portfolio())
        assert rm.consecutive_loss_limit == 3

    def test_default_cooldown_is_720_minutes(self) -> None:
        rm = RiskManager(_make_settings(), _make_portfolio())
        assert rm.consecutive_loss_cooldown_minutes == 720

    def test_properties_are_settable(self) -> None:
        rm = RiskManager(_make_settings(), _make_portfolio())
        rm.consecutive_loss_limit = 5
        rm.consecutive_loss_cooldown_minutes = 30
        assert rm.consecutive_loss_limit == 5
        assert rm.consecutive_loss_cooldown_minutes == 30


class TestRiskManagerCircuitBreakerBehavior:
    def test_does_not_block_below_limit(self) -> None:
        settings = _make_settings()
        state = _make_portfolio()
        rm = RiskManager(settings, state, consecutive_loss_limit=3)
        rm.on_position_closed(Decimal("-100"))
        rm.on_position_closed(Decimal("-100"))
        rm.check_can_open_position()  # 2 losses < limit 3 → should not raise

    def test_blocks_at_limit(self) -> None:
        settings = _make_settings()
        state = _make_portfolio()
        rm = RiskManager(
            settings, state, consecutive_loss_limit=3, consecutive_loss_cooldown_minutes=60
        )
        for _ in range(3):
            rm.on_position_closed(Decimal("-100"))
        with pytest.raises(TradingCooldownError):
            rm.check_can_open_position()

    def test_win_between_losses_resets_and_avoids_cooldown(self) -> None:
        settings = _make_settings()
        state = _make_portfolio()
        rm = RiskManager(settings, state, consecutive_loss_limit=3)
        rm.on_position_closed(Decimal("-100"))
        rm.on_position_closed(Decimal("-100"))
        rm.on_position_closed(Decimal("50"))   # win resets streak
        rm.on_position_closed(Decimal("-100"))  # only 1 consecutive loss now
        rm.check_can_open_position()  # should not raise

    def test_cooldown_expires_after_window_elapses(self) -> None:
        settings = _make_settings()
        state = _make_portfolio()
        rm = RiskManager(
            settings, state, consecutive_loss_limit=2, consecutive_loss_cooldown_minutes=10
        )
        rm.on_position_closed(Decimal("-100"))
        rm.on_position_closed(Decimal("-100"))
        with pytest.raises(TradingCooldownError):
            rm.check_can_open_position()
        # Simulate time passing beyond the cooldown window.
        state.cooldown_until = datetime.now(UTC) - timedelta(minutes=1)
        rm.check_can_open_position()  # cooldown expired → should not raise
        assert state.cooldown_until is None

    def test_disabled_when_limit_is_zero(self) -> None:
        # Small loss amounts so the (unrelated, pre-existing) daily-loss limit
        # doesn't fire first and mask what this test is actually checking.
        settings = _make_settings()
        state = _make_portfolio()
        rm = RiskManager(settings, state, consecutive_loss_limit=0)
        for _ in range(5):
            rm.on_position_closed(Decimal("-10"))
        rm.check_can_open_position()  # circuit breaker disabled → never raises

    def test_backward_compatible_default_construction_never_raises_without_losses(self) -> None:
        # Existing call sites across the codebase construct RiskManager(settings,
        # state) with no extra args and never trigger 3 consecutive losses in a
        # single test — this must keep behaving exactly as before.
        settings = _make_settings()
        state = _make_portfolio()
        rm = RiskManager(settings, state)
        rm.on_position_closed(Decimal("300"))
        rm.check_can_open_position()  # should not raise


class TestOnPositionClosedReturnValue:
    """on_position_closed() returns True only at the exact moment the
    consecutive-loss cooldown is newly triggered — purely observational,
    does not change any risk logic/thresholds."""

    def test_returns_false_when_no_loss(self) -> None:
        rm = RiskManager(_make_settings(), _make_portfolio())
        assert rm.on_position_closed(Decimal("300")) is False

    def test_returns_false_below_limit(self) -> None:
        rm = RiskManager(_make_settings(), _make_portfolio(), consecutive_loss_limit=3)
        assert rm.on_position_closed(Decimal("-100")) is False
        assert rm.on_position_closed(Decimal("-100")) is False

    def test_returns_true_exactly_when_cooldown_newly_triggered(self) -> None:
        rm = RiskManager(
            _make_settings(), _make_portfolio(), consecutive_loss_limit=3,
            consecutive_loss_cooldown_minutes=60,
        )
        assert rm.on_position_closed(Decimal("-100")) is False
        assert rm.on_position_closed(Decimal("-100")) is False
        assert rm.on_position_closed(Decimal("-100")) is True  # 3rd loss triggers it

    def test_returns_false_on_subsequent_losses_while_already_in_cooldown(self) -> None:
        rm = RiskManager(
            _make_settings(), _make_portfolio(), consecutive_loss_limit=2,
            consecutive_loss_cooldown_minutes=60,
        )
        assert rm.on_position_closed(Decimal("-100")) is False
        assert rm.on_position_closed(Decimal("-100")) is True  # triggers
        # Cooldown already active — must not report "newly triggered" again.
        assert rm.on_position_closed(Decimal("-100")) is False

    def test_returns_false_when_disabled(self) -> None:
        rm = RiskManager(_make_settings(), _make_portfolio(), consecutive_loss_limit=0)
        for _ in range(5):
            assert rm.on_position_closed(Decimal("-10")) is False
