"""
Immutable portfolio domain models.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class Position:
    """An open position in the portfolio."""

    symbol: str
    side: Literal["buy", "sell"]
    amount: Decimal              # base currency units
    entry_price: Decimal
    entry_time: datetime
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    trailing_stop_pct: Decimal | None = None   # if set, stop_loss ratchets up
    highest_price: Decimal | None = None       # peak price since entry (ratchet-only, side="buy")

    def unrealized_pnl(self, current_price: Decimal) -> Decimal:
        if self.side == "buy":
            return (current_price - self.entry_price) * self.amount
        return (self.entry_price - current_price) * self.amount

    def unrealized_pnl_pct(self, current_price: Decimal) -> float:
        cost = self.entry_price * self.amount
        if cost == 0:
            return 0.0
        return float(self.unrealized_pnl(current_price) / cost) * 100

    def notional_value(self, current_price: Decimal) -> Decimal:
        return current_price * self.amount


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Point-in-time snapshot of entire portfolio."""

    timestamp: datetime
    cash: Decimal
    positions: tuple[Position, ...]
    realized_pnl: Decimal
    total_commission: Decimal

    def total_unrealized_pnl(self, prices: dict[str, Decimal]) -> Decimal:
        return sum(
            p.unrealized_pnl(prices[p.symbol])
            for p in self.positions
            if p.symbol in prices
        )

    def total_equity(self, prices: dict[str, Decimal]) -> Decimal:
        return self.cash + sum(
            p.notional_value(prices[p.symbol])
            for p in self.positions
            if p.symbol in prices
        )

    @property
    def open_position_count(self) -> int:
        return len(self.positions)
