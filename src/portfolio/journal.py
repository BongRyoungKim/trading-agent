"""
Trade Journal: records completed trades and computes running statistics.

Designed to be integrated with TradingEngine._close_position so that
win_rate, avg_win, avg_loss, and profit_factor are always up-to-date.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.portfolio.journal_store import SQLiteJournalStore


@dataclass(frozen=True)
class TradeRecord:
    """Immutable record of a completed trade."""

    symbol: str
    side: Literal["buy", "sell"]
    amount: Decimal
    entry_price: Decimal
    exit_price: Decimal
    entry_time: datetime
    exit_time: datetime
    pnl: Decimal          # net PnL after commission
    commission: Decimal
    reason: str           # "signal" | "stop_loss" | "take_profit"

    @property
    def is_win(self) -> bool:
        return self.pnl > Decimal("0")

    @property
    def pnl_pct(self) -> float:
        cost = self.entry_price * self.amount
        if cost == 0:
            return 0.0
        return float(self.pnl / cost * 100)


class TradeJournal:
    """
    Accumulates TradeRecords and exposes running statistics.

    Can be backed by a SQLiteJournalStore so trades survive engine restarts.

    Usage (in-memory):
        journal = TradeJournal()

    Usage (persistent):
        store = SQLiteJournalStore()
        journal = TradeJournal.from_store(store)  # pre-loads history
        journal.record(trade)   # auto-persists
    """

    def __init__(self, store: SQLiteJournalStore | None = None) -> None:
        self._trades: list[TradeRecord] = []
        self._store = store

    @classmethod
    def from_store(cls, store: SQLiteJournalStore) -> TradeJournal:
        """
        Create a journal pre-loaded with all trades from the store.
        New trades recorded will be automatically persisted.
        """
        journal = cls(store=store)
        journal._trades = store.load_all()
        return journal

    # ── Write ─────────────────────────────────────────────────────────────────

    def record(self, trade: TradeRecord) -> None:
        """Append a completed trade. Persists to store if one is attached."""
        if self._store is not None:
            self._store.save(trade)
        self._trades.append(trade)

    # ── Read ──────────────────────────────────────────────────────────────────

    @property
    def trades(self) -> list[TradeRecord]:
        return list(self._trades)

    @property
    def total_trades(self) -> int:
        return len(self._trades)

    def stats(self) -> dict:
        """
        Return a statistics summary dict:
          total_trades, wins, losses, win_rate_pct,
          avg_win, avg_loss, profit_factor, total_pnl
        """
        if not self._trades:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate_pct": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "total_pnl": 0.0,
            }

        wins = [t for t in self._trades if t.is_win]
        losses = [t for t in self._trades if not t.is_win]

        total_win = sum(t.pnl for t in wins)
        total_loss = abs(sum(t.pnl for t in losses))

        win_rate = len(wins) / len(self._trades) * 100
        avg_win = float(total_win / len(wins)) if wins else 0.0
        avg_loss = float(total_loss / len(losses)) if losses else 0.0
        profit_factor = float(total_win / total_loss) if total_loss > 0 else float("inf")

        return {
            "total_trades": len(self._trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate, 2),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else profit_factor,
            "total_pnl": float(sum(t.pnl for t in self._trades)),
        }

    def recent(self, n: int = 10) -> list[TradeRecord]:
        """Return the last N completed trades (most recent last)."""
        return self._trades[-n:]

    def for_symbol(self, symbol: str) -> list[TradeRecord]:
        """Return all trades for a specific symbol."""
        return [t for t in self._trades if t.symbol == symbol]
