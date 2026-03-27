"""
Immutable models for backtest trades and results.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class Trade:
    """A completed round-trip trade (entry + exit)."""

    symbol: str
    side: Literal["buy", "sell"]       # direction of the entry
    entry_price: Decimal
    exit_price: Decimal
    amount: Decimal                    # base currency quantity
    entry_time: datetime
    exit_time: datetime
    commission: Decimal                # total commission paid (entry + exit)

    @property
    def pnl(self) -> Decimal:
        """Gross PnL before commission."""
        if self.side == "buy":
            return (self.exit_price - self.entry_price) * self.amount
        return (self.entry_price - self.exit_price) * self.amount

    @property
    def net_pnl(self) -> Decimal:
        """PnL after commission."""
        return self.pnl - self.commission

    @property
    def return_pct(self) -> float:
        """Net return as a percentage of capital at entry."""
        capital = self.entry_price * self.amount
        if capital == 0:
            return 0.0
        return float(self.net_pnl / capital) * 100

    @property
    def is_winner(self) -> bool:
        return self.net_pnl > 0


@dataclass(frozen=True)
class BacktestResult:
    """Aggregated results from a completed backtest run."""

    strategy_name: str
    symbol: str
    timeframe: str
    start_date: datetime
    end_date: datetime
    initial_capital: Decimal
    final_capital: Decimal
    trades: tuple[Trade, ...]          # immutable sequence

    # Pre-computed metrics (filled by MetricsCalculator)
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0

    def summary(self) -> str:
        return (
            f"Strategy: {self.strategy_name} | {self.symbol} {self.timeframe}\n"
            f"Period: {self.start_date.date()} → {self.end_date.date()}\n"
            f"Capital: {self.initial_capital:,.2f} → {self.final_capital:,.2f}\n"
            f"Total Return: {self.total_return_pct:+.2f}%\n"
            f"Max Drawdown: {self.max_drawdown_pct:.2f}%\n"
            f"Sharpe Ratio: {self.sharpe_ratio:.3f}\n"
            f"Win Rate: {self.win_rate_pct:.1f}%  |  Total Trades: {self.total_trades}\n"
            f"Profit Factor: {self.profit_factor:.2f}"
        )
