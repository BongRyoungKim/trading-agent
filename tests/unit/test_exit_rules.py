"""Unit tests for src/risk/exit_rules.py — the shared exit-rule module
extracted from TradingEngine. Each test pins down the exact arithmetic that
used to live inline in engine.py, so any future edit that accidentally
changes the math (in either engine.py or the backtest engine) gets caught.
"""
from __future__ import annotations

from decimal import Decimal

from src.risk.exit_rules import (
    ExitDecision,
    clamp_stop_loss_and_size_take_profit,
    evaluate_open_position_exit,
    is_signal_exit_allowed,
    update_trailing_stop,
)


# ── clamp_stop_loss_and_size_take_profit ────────────────────────────────────

class TestClampStopLossAndSizeTakeProfit:
    def test_raw_sl_tighter_than_ceiling_gets_widened(self) -> None:
        # entry 100,000, ceiling=1.5% -> ceiling price 98,500.
        # raw SL at 99,500 (0.5% away) is *tighter* than the ceiling allows.
        stop_loss, take_profit = clamp_stop_loss_and_size_take_profit(
            entry_price=Decimal("100000"),
            raw_stop_loss=Decimal("99500"),
            sl_ceiling_pct=Decimal("1.5"),
            sl_floor_pct=Decimal("3.0"),
            tp_rr_multiplier=Decimal("1.5"),
        )
        assert stop_loss == Decimal("98500")
        assert take_profit == Decimal("100000") + Decimal("1500") * Decimal("1.5")

    def test_raw_sl_looser_than_floor_gets_tightened(self) -> None:
        # floor=3.0% -> floor price 97,000. raw SL at 95,000 is looser (further).
        stop_loss, take_profit = clamp_stop_loss_and_size_take_profit(
            entry_price=Decimal("100000"),
            raw_stop_loss=Decimal("95000"),
            sl_ceiling_pct=Decimal("1.5"),
            sl_floor_pct=Decimal("3.0"),
            tp_rr_multiplier=Decimal("1.5"),
        )
        assert stop_loss == Decimal("97000")
        assert take_profit == Decimal("100000") + Decimal("3000") * Decimal("1.5")

    def test_raw_sl_within_bounds_passes_through_unchanged(self) -> None:
        # 2% away from entry, between ceiling(1.5%) and floor(3.0%).
        stop_loss, take_profit = clamp_stop_loss_and_size_take_profit(
            entry_price=Decimal("100000"),
            raw_stop_loss=Decimal("98000"),
            sl_ceiling_pct=Decimal("1.5"),
            sl_floor_pct=Decimal("3.0"),
            tp_rr_multiplier=Decimal("2.0"),
        )
        assert stop_loss == Decimal("98000")
        assert take_profit == Decimal("100000") + Decimal("2000") * Decimal("2.0")


# ── update_trailing_stop ─────────────────────────────────────────────────────

class TestUpdateTrailingStop:
    def test_none_pct_returns_current_unchanged(self) -> None:
        result = update_trailing_stop(
            current_price=Decimal("110000"), trailing_stop_pct=None,
            current_stop_loss=Decimal("95000"),
        )
        assert result == Decimal("95000")

    def test_sell_side_never_ratchets(self) -> None:
        result = update_trailing_stop(
            current_price=Decimal("110000"), trailing_stop_pct=Decimal("2.0"),
            current_stop_loss=Decimal("95000"), side="sell",
        )
        assert result == Decimal("95000")

    def test_ratchets_up_when_new_trail_is_higher(self) -> None:
        # price 110,000, trailing 2% -> new_trail 107,800 > current 95,000
        result = update_trailing_stop(
            current_price=Decimal("110000"), trailing_stop_pct=Decimal("2.0"),
            current_stop_loss=Decimal("95000"),
        )
        assert result == Decimal("107800")

    def test_never_ratchets_down(self) -> None:
        # price drops back; new_trail would be lower than the already-set SL.
        result = update_trailing_stop(
            current_price=Decimal("100000"), trailing_stop_pct=Decimal("2.0"),
            current_stop_loss=Decimal("107800"),
        )
        assert result == Decimal("107800")

    def test_none_current_stop_loss_sets_first_trail(self) -> None:
        result = update_trailing_stop(
            current_price=Decimal("100000"), trailing_stop_pct=Decimal("2.0"),
            current_stop_loss=None,
        )
        assert result == Decimal("98000")


# ── evaluate_open_position_exit ──────────────────────────────────────────────

class TestEvaluateOpenPositionExit:
    def test_stop_loss_triggers(self) -> None:
        decision = evaluate_open_position_exit(
            entry_price=Decimal("100000"), hold_minutes=10.0,
            current_price=Decimal("98000"),
            stop_loss=Decimal("98500"), take_profit=Decimal("105000"),
            trailing_stop_pct=None,
        )
        assert decision == ExitDecision(True, "stop_loss", Decimal("98500"))

    def test_take_profit_triggers(self) -> None:
        decision = evaluate_open_position_exit(
            entry_price=Decimal("100000"), hold_minutes=10.0,
            current_price=Decimal("106000"),
            stop_loss=Decimal("98500"), take_profit=Decimal("105000"),
            trailing_stop_pct=None,
        )
        assert decision == ExitDecision(True, "take_profit", Decimal("98500"))

    def test_time_stop_triggers_after_grace_period_when_losing(self) -> None:
        # entry 100,000; 0.5% threshold -> 99,500. price 99,000 < 99,500 and
        # hold_minutes(61) >= time_stop_minutes(60).
        decision = evaluate_open_position_exit(
            entry_price=Decimal("100000"), hold_minutes=61.0,
            current_price=Decimal("99000"),
            stop_loss=Decimal("95000"), take_profit=Decimal("105000"),
            trailing_stop_pct=None,
        )
        assert decision.should_exit is True
        assert decision.reason == "time_stop"

    def test_time_stop_does_not_trigger_before_grace_period(self) -> None:
        decision = evaluate_open_position_exit(
            entry_price=Decimal("100000"), hold_minutes=30.0,
            current_price=Decimal("99000"),
            stop_loss=Decimal("95000"), take_profit=Decimal("105000"),
            trailing_stop_pct=None,
        )
        assert decision.should_exit is False

    def test_time_stop_does_not_trigger_if_not_losing_enough(self) -> None:
        # price 99,600 is only -0.4%, above the -0.5% time-stop threshold.
        decision = evaluate_open_position_exit(
            entry_price=Decimal("100000"), hold_minutes=90.0,
            current_price=Decimal("99600"),
            stop_loss=Decimal("95000"), take_profit=Decimal("105000"),
            trailing_stop_pct=None,
        )
        assert decision.should_exit is False

    def test_no_exit_when_nothing_triggers(self) -> None:
        decision = evaluate_open_position_exit(
            entry_price=Decimal("100000"), hold_minutes=5.0,
            current_price=Decimal("100500"),
            stop_loss=Decimal("98500"), take_profit=Decimal("105000"),
            trailing_stop_pct=None,
        )
        assert decision == ExitDecision(False, None, Decimal("98500"))

    def test_check_order_stop_loss_wins_over_take_profit(self) -> None:
        # Degenerate/inverted levels where both conditions could read as true
        # (should never happen with valid SL<entry<TP, but pins the order).
        decision = evaluate_open_position_exit(
            entry_price=Decimal("100000"), hold_minutes=5.0,
            current_price=Decimal("100000"),
            stop_loss=Decimal("100000"), take_profit=Decimal("100000"),
            trailing_stop_pct=None,
        )
        assert decision.reason == "stop_loss"

    def test_trailing_ratchet_reflected_in_updated_stop_loss_without_exit(self) -> None:
        decision = evaluate_open_position_exit(
            entry_price=Decimal("100000"), hold_minutes=5.0,
            current_price=Decimal("110000"),
            stop_loss=Decimal("95000"), take_profit=Decimal("120000"),
            trailing_stop_pct=Decimal("2.0"),
        )
        assert decision.should_exit is False
        assert decision.updated_stop_loss == Decimal("107800")

    def test_custom_time_stop_params_override_defaults(self) -> None:
        decision = evaluate_open_position_exit(
            entry_price=Decimal("100000"), hold_minutes=120.0,
            current_price=Decimal("99900"),
            stop_loss=Decimal("90000"), take_profit=Decimal("110000"),
            trailing_stop_pct=None,
            time_stop_minutes=300.0, time_stop_loss_pct=Decimal("0.5"),
        )
        # hold_minutes(120) < time_stop_minutes(300) -> no time-stop yet
        assert decision.should_exit is False


# ── is_signal_exit_allowed ────────────────────────────────────────────────────

class TestIsSignalExitAllowed:
    def test_blocked_before_min_hold(self) -> None:
        assert is_signal_exit_allowed(hold_seconds=600.0, unrealized_pct=1.0) is False

    def test_allowed_after_min_hold_with_unknown_pnl(self) -> None:
        assert is_signal_exit_allowed(hold_seconds=1800.0, unrealized_pct=None) is True

    def test_blocked_when_profit_below_threshold(self) -> None:
        assert is_signal_exit_allowed(hold_seconds=2000.0, unrealized_pct=0.1) is False

    def test_allowed_when_profit_meets_threshold(self) -> None:
        assert is_signal_exit_allowed(hold_seconds=2000.0, unrealized_pct=0.3) is True

    def test_custom_thresholds(self) -> None:
        assert is_signal_exit_allowed(
            hold_seconds=100.0, unrealized_pct=5.0,
            min_hold_seconds=50.0, min_profit_pct=1.0,
        ) is True
