"""
Risk Manager: enforces risk limits before any order is placed.
Acts as a hard gate — trading engine must call check_* methods before execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger

from src.config.settings import Settings
from src.utils.exceptions import (
    MaxDrawdownExceededError,
    PositionLimitExceededError,
    TradingCooldownError,
)


@dataclass
class PortfolioState:
    """
    Mutable snapshot of current portfolio state.
    Updated by the trading engine after each fill.
    """
    capital: Decimal
    peak_capital: Decimal
    open_positions: int = 0
    daily_loss: Decimal = Decimal("0")
    last_reset_date: datetime = field(default_factory=lambda: datetime.now(UTC))
    consecutive_losses: int = 0
    cooldown_until: datetime | None = None

    @property
    def current_drawdown_pct(self) -> float:
        if self.peak_capital == 0:
            return 0.0
        return float((self.peak_capital - self.capital) / self.peak_capital)

    def update_peak(self) -> None:
        if self.capital > self.peak_capital:
            self.peak_capital = self.capital

    def record_trade_result(self, pnl: Decimal) -> None:
        """Update capital, daily loss, and the consecutive-loss streak after
        a trade closes. A strictly negative pnl extends the loss streak; a
        zero or positive pnl resets it (mirrors the existing `pnl < 0` rule
        used for daily_loss)."""
        self.capital += pnl
        self.update_peak()
        if pnl < 0:
            self.daily_loss += abs(pnl)
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def reset_daily_loss_if_new_day(self) -> None:
        today = datetime.now(UTC).date()
        if self.last_reset_date.date() < today:
            self.daily_loss = Decimal("0")
            self.last_reset_date = datetime.now(UTC)


class RiskManager:
    """
    Enforces risk limits before order placement.

    Usage:
        risk = RiskManager(settings, portfolio_state)

        # Before placing a buy order:
        risk.check_can_open_position(order_size_usd)

        # After a trade closes:
        risk.on_trade_closed(pnl)

    Consecutive-loss circuit breaker:
        `consecutive_loss_limit`/`consecutive_loss_cooldown_minutes` are
        constructor args (not Settings fields) so they follow the same
        live-tunable-knob pattern as TradingEngine's sl_ceiling_pct etc. —
        settable via properties after construction, wired from CLI args /
        `.strategy_params.json` in src/main.py. Keeping them off `Settings`
        also avoids a MagicMock-settings footgun in tests elsewhere in the
        suite that build `RiskManager` with a bare `MagicMock()` and never
        set unrelated risk fields explicitly.
    """

    def __init__(
        self,
        settings: Settings,
        state: PortfolioState,
        consecutive_loss_limit: int = 3,
        consecutive_loss_cooldown_minutes: int = 720,
    ) -> None:
        self._settings = settings
        self._state = state
        self._consecutive_loss_limit = consecutive_loss_limit
        self._consecutive_loss_cooldown_minutes = consecutive_loss_cooldown_minutes

    # ── Consecutive-loss circuit breaker config ──────────────────────────────

    @property
    def consecutive_loss_limit(self) -> int:
        """Consecutive net-losing trades that trigger a cooldown. 0 disables it."""
        return self._consecutive_loss_limit

    @consecutive_loss_limit.setter
    def consecutive_loss_limit(self, value: int) -> None:
        self._consecutive_loss_limit = value

    @property
    def consecutive_loss_cooldown_minutes(self) -> int:
        """Minutes new entries stay blocked once the loss streak hits the limit."""
        return self._consecutive_loss_cooldown_minutes

    @consecutive_loss_cooldown_minutes.setter
    def consecutive_loss_cooldown_minutes(self, value: int) -> None:
        self._consecutive_loss_cooldown_minutes = value

    # ── Checks (raise on violation) ──────────────────────────────────────────

    def check_drawdown(self) -> None:
        """
        Raise MaxDrawdownExceededError if current drawdown exceeds halt threshold.
        CRITICAL: Call this before every order.
        """
        self._state.reset_daily_loss_if_new_day()
        dd = self._state.current_drawdown_pct
        limit = self._settings.max_drawdown_halt

        if dd >= limit:
            raise MaxDrawdownExceededError(
                f"Trading halted: drawdown {dd:.1%} >= limit {limit:.1%}",
                details={
                    "drawdown_pct": round(dd * 100, 2),
                    "limit_pct": round(limit * 100, 2),
                    "capital": str(self._state.capital),
                    "peak_capital": str(self._state.peak_capital),
                },
            )

    def check_daily_loss(self) -> None:
        """Raise if daily loss limit has been reached."""
        self._state.reset_daily_loss_if_new_day()
        daily_loss_pct = (
            float(self._state.daily_loss / self._state.peak_capital)
            if self._state.peak_capital > 0
            else 0.0
        )
        limit = self._settings.max_daily_loss

        if daily_loss_pct >= limit:
            raise MaxDrawdownExceededError(
                f"Daily loss limit reached: {daily_loss_pct:.1%} >= {limit:.1%}",
                details={
                    "daily_loss_pct": round(daily_loss_pct * 100, 2),
                    "limit_pct": round(limit * 100, 2),
                },
            )

    def check_position_limit(self) -> None:
        """Raise if max concurrent positions would be exceeded."""
        limit = self._settings.max_open_positions
        if self._state.open_positions >= limit:
            raise PositionLimitExceededError(
                f"Position limit reached: {self._state.open_positions}/{limit} positions open",
                details={
                    "open_positions": self._state.open_positions,
                    "limit": limit,
                },
            )

    def check_cooldown(self) -> None:
        """
        Raise TradingCooldownError if a consecutive-loss cooldown is active.

        Unlike the other check_* methods this is not a hard violation of a
        risk limit — it is a temporary, self-clearing pause after a losing
        streak. Once the cooldown window has elapsed this clears
        `cooldown_until` and stops raising.
        """
        cooldown_until = self._state.cooldown_until
        if cooldown_until is None:
            return
        now = datetime.now(UTC)
        if now < cooldown_until:
            remaining_min = (cooldown_until - now).total_seconds() / 60
            raise TradingCooldownError(
                f"Circuit breaker active: {self._state.consecutive_losses} consecutive "
                f"losses, {remaining_min:.0f} more minutes until entries resume",
                details={
                    "consecutive_losses": self._state.consecutive_losses,
                    "cooldown_until": cooldown_until.isoformat(),
                    "remaining_minutes": round(remaining_min, 1),
                },
            )
        self._state.cooldown_until = None

    def check_can_open_position(self) -> None:
        """
        Run all pre-trade checks. Raises on first violation.
        Trading engine must call this before every BUY order.
        """
        self.check_drawdown()
        self.check_daily_loss()
        self.check_position_limit()
        self.check_cooldown()
        logger.debug(
            "Risk checks passed",
            open_positions=self._state.open_positions,
            drawdown_pct=round(self._state.current_drawdown_pct * 100, 2),
        )

    # ── State update callbacks ────────────────────────────────────────────────

    def on_position_opened(self) -> None:
        self._state.open_positions += 1
        logger.debug("Position opened", open_positions=self._state.open_positions)

    def on_position_closed(self, pnl: Decimal) -> None:
        self._state.open_positions = max(0, self._state.open_positions - 1)
        self._state.record_trade_result(pnl)

        if (
            self._consecutive_loss_limit > 0
            and self._state.consecutive_losses >= self._consecutive_loss_limit
            and self._state.cooldown_until is None
        ):
            self._state.cooldown_until = datetime.now(UTC) + timedelta(
                minutes=self._consecutive_loss_cooldown_minutes
            )
            logger.warning(
                "Consecutive-loss circuit breaker triggered — new entries paused",
                consecutive_losses=self._state.consecutive_losses,
                cooldown_until=self._state.cooldown_until.isoformat(),
            )

        logger.info(
            "Position closed",
            pnl=float(pnl),
            capital=float(self._state.capital),
            drawdown_pct=round(self._state.current_drawdown_pct * 100, 2),
        )

    # ── Stop-loss helpers ─────────────────────────────────────────────────────

    def calculate_stop_loss(
        self,
        entry_price: Decimal,
        side: str = "buy",
        atr_value: float | None = None,
        atr_multiplier: float = 2.0,
        fixed_pct: float | None = None,
    ) -> Decimal:
        """
        Calculate stop-loss price.

        Priority:
            1. ATR-based stop if atr_value provided.
            2. Fixed percentage stop if fixed_pct provided.
            3. Default: max_position_risk * entry_price.

        Args:
            entry_price:     Entry price.
            side:            'buy' (long) or 'sell' (short).
            atr_value:       Current ATR value in price units.
            atr_multiplier:  Multiplier on ATR (default 2.0).
            fixed_pct:       Fixed percentage below/above entry (e.g. 0.02 = 2%).

        Returns:
            Stop-loss price.
        """
        if atr_value is not None:
            stop_distance = Decimal(str(atr_value * atr_multiplier))
        elif fixed_pct is not None:
            stop_distance = entry_price * Decimal(str(fixed_pct))
        else:
            stop_distance = entry_price * Decimal(str(self._settings.max_position_risk))

        if side == "buy":
            return max(Decimal("0"), entry_price - stop_distance)
        return entry_price + stop_distance
