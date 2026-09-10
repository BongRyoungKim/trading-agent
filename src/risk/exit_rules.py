"""
Exit-rule calculations shared between the live TradingEngine and the
backtest engine (src/backtest/exit_backtest_engine.py), so backtests can
never structurally diverge from what the live engine actually does.

This module is a pure extraction from src/engine.py's
TradingEngine._open_position / ._check_exit_conditions / ._process_symbol —
a relocation refactor, not a reimplementation. Every formula here is
byte-for-byte identical to what was previously inlined in engine.py,
including its exact numeric types (Decimal for price/SL/TP/trailing math,
plain float for the signal-exit gate — engine.py already mixed the two, and
this extraction preserves that instead of "cleaning it up").

Pure functions/dataclasses only: no I/O, no exchange calls, no mutation of
caller-owned objects (Position, PortfolioTracker, etc). Callers (engine.py,
the backtest engine) own all state and apply whatever this module returns.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

ExitReason = Literal["stop_loss", "take_profit", "time_stop"]


@dataclass(frozen=True)
class ExitDecision:
    """Result of evaluating SL/TP/trailing/time-stop for one open position."""

    should_exit: bool
    reason: ExitReason | None
    updated_stop_loss: Decimal | None
    # ^ the trailing-ratcheted stop-loss to persist on the position even when
    #   should_exit is False (trailing can move the SL up without closing).


def clamp_stop_loss_and_size_take_profit(
    entry_price: Decimal,
    raw_stop_loss: Decimal,
    sl_ceiling_pct: Decimal,
    sl_floor_pct: Decimal,
    tp_rr_multiplier: Decimal,
) -> tuple[Decimal, Decimal]:
    """
    Extracted verbatim from TradingEngine._open_position.

    Clamp `raw_stop_loss` (typically ATR-based, from RiskManager.
    calculate_stop_loss) into [sl_ceiling_pct, sl_floor_pct]% below entry:
      - sl_ceiling_pct (min distance): widens noise-tight stops.
      - sl_floor_pct   (max distance): caps the worst-case loss per trade.
    Then size take-profit as tp_rr_multiplier x the *clamped* SL distance.

    Returns (stop_loss, take_profit).
    """
    sl_ceiling = entry_price * (Decimal("1") - sl_ceiling_pct / Decimal("100"))
    sl_floor = entry_price * (Decimal("1") - sl_floor_pct / Decimal("100"))

    stop_loss = raw_stop_loss
    if stop_loss > sl_ceiling:
        stop_loss = sl_ceiling
    if stop_loss < sl_floor:
        stop_loss = sl_floor

    sl_distance = entry_price - stop_loss
    take_profit = entry_price + sl_distance * tp_rr_multiplier
    return stop_loss, take_profit


def update_trailing_stop(
    current_price: Decimal,
    trailing_stop_pct: Decimal | None,
    current_stop_loss: Decimal | None,
    side: Literal["buy", "sell"] = "buy",
) -> Decimal | None:
    """
    Extracted verbatim from TradingEngine._check_exit_conditions's trailing
    stop ratchet. Only ratchets for `side == "buy"` (matches the original,
    which never supported trailing on short/sell positions). The stop-loss
    only ever moves up (toward the current price), never down.
    """
    if trailing_stop_pct is None or side != "buy":
        return current_stop_loss
    new_trail = current_price * (Decimal("1") - trailing_stop_pct / Decimal("100"))
    if current_stop_loss is None or new_trail > current_stop_loss:
        return new_trail
    return current_stop_loss


def evaluate_open_position_exit(
    *,
    entry_price: Decimal,
    hold_minutes: float,
    current_price: Decimal,
    stop_loss: Decimal | None,
    take_profit: Decimal | None,
    trailing_stop_pct: Decimal | None,
    side: Literal["buy", "sell"] = "buy",
    time_stop_minutes: float = 60.0,
    time_stop_loss_pct: Decimal = Decimal("0.5"),
) -> ExitDecision:
    """
    Extracted verbatim from TradingEngine._check_exit_conditions, preserving
    the exact original check order: trailing ratchet -> stop-loss ->
    take-profit -> time-stop.

    Deliberately NOT extracted (these stay in engine.py as portfolio/
    dashboard bookkeeping, not exit-rule math):
      - dust position removal (value <= a KRW threshold)
      - the "highest_price" ratchet used only for dashboard display

    `time_stop_minutes`/`time_stop_loss_pct` default to 60 / 0.5%, matching
    engine.py's previously-hardcoded "cut losers after 60 min at -0.5%"
    constants exactly — callers may override, but engine.py's own call site
    keeps passing these same defaults (no behavior change).
    """
    updated_stop_loss = update_trailing_stop(current_price, trailing_stop_pct, stop_loss, side)

    if updated_stop_loss is not None and current_price <= updated_stop_loss:
        return ExitDecision(True, "stop_loss", updated_stop_loss)

    if take_profit is not None and current_price >= take_profit:
        return ExitDecision(True, "take_profit", updated_stop_loss)

    if hold_minutes >= time_stop_minutes and current_price < entry_price * (
        Decimal("1") - time_stop_loss_pct / Decimal("100")
    ):
        return ExitDecision(True, "time_stop", updated_stop_loss)

    return ExitDecision(False, None, updated_stop_loss)


def is_signal_exit_allowed(
    hold_seconds: float,
    unrealized_pct: float | None,
    min_hold_seconds: float = 1800.0,
    min_profit_pct: float = 0.3,
) -> bool:
    """
    Extracted verbatim from TradingEngine._process_symbol's SELL-signal gate.

    A signal-driven SELL is only honoured once the position has been held
    for at least `min_hold_seconds` AND (when `unrealized_pct` is known) the
    unrealized gain covers at least `min_profit_pct` (i.e. commissions).

    Note: matches engine.py's original float arithmetic exactly (prices are
    converted to float before this check in engine.py) rather than Decimal,
    which the SL/TP/trailing functions above use — preserved as-is rather
    than "cleaned up", per the no-logic-change constraint on this refactor.
    """
    if hold_seconds < min_hold_seconds:
        return False
    if unrealized_pct is not None and unrealized_pct < min_profit_pct:
        return False
    return True
