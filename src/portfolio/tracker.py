"""
Portfolio tracker: manages open positions and realized PnL.
Thread-safe for single-threaded use (Phase 9 engine runs on one thread).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from loguru import logger

from src.portfolio.models import Position, PortfolioSnapshot
from src.utils.exceptions import TradingAgentError

if TYPE_CHECKING:
    from src.portfolio.position_store import SQLitePositionStore


class PortfolioTracker:
    """
    Tracks open positions and cumulative PnL.

    Usage:
        tracker = PortfolioTracker(initial_cash=Decimal("10000"))
        tracker.open_position("BTC/USDT", "buy", Decimal("0.01"), Decimal("50000"))
        snapshot = tracker.snapshot(prices={"BTC/USDT": Decimal("52000")})
        tracker.close_position("BTC/USDT", Decimal("52000"), commission=Decimal("5"))
    """

    def __init__(
        self,
        initial_cash: Decimal,
        store: SQLitePositionStore | None = None,
    ) -> None:
        self._cash = initial_cash
        self._positions: dict[str, Position] = {}
        self._realized_pnl = Decimal("0")
        self._total_commission = Decimal("0")
        self._store = store

    @classmethod
    def from_store(
        cls,
        initial_cash: Decimal,
        store: SQLitePositionStore,
    ) -> PortfolioTracker:
        """
        Create a tracker pre-loaded with open positions from the store.

        ``initial_cash`` is adjusted downward by the notional cost of each
        restored position so that equity accounting stays correct.
        New positions will be automatically persisted.
        """
        tracker = cls(initial_cash=initial_cash, store=store)
        restored = store.load_all()
        for symbol, pos in restored.items():
            cost = pos.entry_price * pos.amount
            tracker._cash -= cost
            tracker._positions[symbol] = pos
            logger.info(
                "Position restored from store",
                symbol=symbol,
                entry_price=float(pos.entry_price),
                amount=float(pos.amount),
            )
        if restored:
            logger.info("Portfolio restored", open_positions=len(restored))
        return tracker

    # ── Position lifecycle ────────────────────────────────────────────────────

    def open_position(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        amount: Decimal,
        entry_price: Decimal,
        commission: Decimal = Decimal("0"),
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        trailing_stop_pct: Decimal | None = None,
    ) -> Position:
        """
        Record a new open position. Deducts cost from cash.

        Raises:
            TradingAgentError: If a position for `symbol` already exists.
        """
        if symbol in self._positions:
            raise TradingAgentError(
                f"Position for {symbol} already open. Close it first.",
                details={"symbol": symbol},
            )

        cost = entry_price * amount + commission
        self._cash -= cost
        self._total_commission += commission

        position = Position(
            symbol=symbol,
            side=side,
            amount=amount,
            entry_price=entry_price,
            entry_time=datetime.now(UTC),
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_stop_pct=trailing_stop_pct,
        )
        self._positions[symbol] = position
        if self._store is not None:
            self._store.save(position)

        logger.info(
            "Position opened",
            symbol=symbol,
            side=side,
            amount=float(amount),
            entry_price=float(entry_price),
            cash_after=float(self._cash),
        )
        return position

    def close_position(
        self,
        symbol: str,
        exit_price: Decimal,
        commission: Decimal = Decimal("0"),
    ) -> Decimal:
        """
        Close an open position. Returns realized net PnL.

        Raises:
            TradingAgentError: If no open position for `symbol`.
        """
        if symbol not in self._positions:
            raise TradingAgentError(
                f"No open position for {symbol}",
                details={"symbol": symbol},
            )

        position = self._positions.pop(symbol)
        if self._store is not None:
            self._store.delete(symbol)
        gross_pnl = position.unrealized_pnl(exit_price)
        net_pnl = gross_pnl - commission

        proceeds = exit_price * position.amount - commission
        self._cash += proceeds
        self._realized_pnl += net_pnl
        self._total_commission += commission

        logger.info(
            "Position closed",
            symbol=symbol,
            entry_price=float(position.entry_price),
            exit_price=float(exit_price),
            net_pnl=float(net_pnl),
            realized_total=float(self._realized_pnl),
        )
        return net_pnl

    def update_stop_loss(self, symbol: str, new_stop: Decimal) -> Position:
        """
        Replace the stop_loss on an open position (ratchet — caller must
        ensure new_stop is more favourable than the current one).

        Returns the updated Position.

        Raises:
            TradingAgentError: If no open position for `symbol`.
        """
        if symbol not in self._positions:
            raise TradingAgentError(
                f"No open position for {symbol}",
                details={"symbol": symbol},
            )
        updated = replace(self._positions[symbol], stop_loss=new_stop)
        self._positions[symbol] = updated
        if self._store is not None:
            self._store.save(updated)
        logger.debug(
            "Trailing stop updated",
            symbol=symbol,
            new_stop=float(new_stop),
        )
        return updated

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    def open_symbols(self) -> list[str]:
        return list(self._positions.keys())

    def snapshot(self, prices: dict[str, Decimal] | None = None) -> PortfolioSnapshot:
        """Return an immutable snapshot of the current portfolio state."""
        return PortfolioSnapshot(
            timestamp=datetime.now(UTC),
            cash=self._cash,
            positions=tuple(self._positions.values()),
            realized_pnl=self._realized_pnl,
            total_commission=self._total_commission,
        )

    @property
    def cash(self) -> Decimal:
        return self._cash

    @property
    def realized_pnl(self) -> Decimal:
        return self._realized_pnl

    @property
    def total_commission(self) -> Decimal:
        return self._total_commission

    def pnl_report(self, prices: dict[str, Decimal] | None = None) -> dict:
        """Return a dict summary suitable for logging or display."""
        prices = prices or {}
        unrealized = sum(
            float(pos.unrealized_pnl(prices.get(sym, pos.entry_price)))
            for sym, pos in self._positions.items()
        )
        return {
            "cash": float(self._cash),
            "realized_pnl": float(self._realized_pnl),
            "unrealized_pnl": unrealized,
            "total_commission": float(self._total_commission),
            "open_positions": len(self._positions),
            "open_symbols": self.open_symbols(),
        }
