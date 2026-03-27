"""Unit tests for TradeJournal."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.portfolio.journal import TradeJournal, TradeRecord


# ── Helpers ───────────────────────────────────────────────────────────────────

_T0 = datetime(2024, 1, 1, tzinfo=UTC)
_T1 = datetime(2024, 1, 2, tzinfo=UTC)


def _trade(
    symbol: str = "BTC/USDT",
    pnl: float = 100.0,
    entry: float = 50000.0,
    exit_: float = 51000.0,
    amount: float = 0.01,
    reason: str = "signal",
) -> TradeRecord:
    return TradeRecord(
        symbol=symbol,
        side="buy",
        amount=Decimal(str(amount)),
        entry_price=Decimal(str(entry)),
        exit_price=Decimal(str(exit_)),
        entry_time=_T0,
        exit_time=_T1,
        pnl=Decimal(str(pnl)),
        commission=Decimal("0"),
        reason=reason,
    )


# ── TradeRecord ───────────────────────────────────────────────────────────────

class TestTradeRecord:
    def test_is_win_positive_pnl(self):
        assert _trade(pnl=10.0).is_win is True

    def test_is_win_negative_pnl(self):
        assert _trade(pnl=-10.0).is_win is False

    def test_is_win_zero_pnl(self):
        assert _trade(pnl=0.0).is_win is False

    def test_pnl_pct(self):
        # entry=50000, amount=0.01 → cost=500; pnl=50 → 10%
        t = _trade(pnl=50.0, entry=50000.0, amount=0.01)
        assert abs(t.pnl_pct - 10.0) < 1e-6

    def test_pnl_pct_zero_amount(self):
        t = TradeRecord(
            symbol="X/Y",
            side="buy",
            amount=Decimal("0"),
            entry_price=Decimal("0"),
            exit_price=Decimal("0"),
            entry_time=_T0,
            exit_time=_T1,
            pnl=Decimal("0"),
            commission=Decimal("0"),
            reason="signal",
        )
        assert t.pnl_pct == 0.0

    def test_record_is_frozen(self):
        t = _trade()
        with pytest.raises((AttributeError, TypeError)):
            t.pnl = Decimal("999")  # type: ignore


# ── Empty journal ─────────────────────────────────────────────────────────────

class TestEmptyJournal:
    def setup_method(self):
        self.j = TradeJournal()

    def test_total_trades_zero(self):
        assert self.j.total_trades == 0

    def test_trades_list_empty(self):
        assert self.j.trades == []

    def test_stats_all_zero(self):
        s = self.j.stats()
        assert s["total_trades"] == 0
        assert s["wins"] == 0
        assert s["losses"] == 0
        assert s["win_rate_pct"] == 0.0
        assert s["avg_win"] == 0.0
        assert s["avg_loss"] == 0.0
        assert s["profit_factor"] == 0.0
        assert s["total_pnl"] == 0.0

    def test_recent_returns_empty(self):
        assert self.j.recent() == []

    def test_for_symbol_returns_empty(self):
        assert self.j.for_symbol("BTC/USDT") == []


# ── Recording and stats ───────────────────────────────────────────────────────

class TestJournalStats:
    def test_single_win_trade(self):
        j = TradeJournal()
        j.record(_trade(pnl=100.0))
        s = j.stats()
        assert s["total_trades"] == 1
        assert s["wins"] == 1
        assert s["losses"] == 0
        assert s["win_rate_pct"] == 100.0
        assert s["profit_factor"] == float("inf")

    def test_single_loss_trade(self):
        j = TradeJournal()
        j.record(_trade(pnl=-50.0))
        s = j.stats()
        assert s["total_trades"] == 1
        assert s["wins"] == 0
        assert s["losses"] == 1
        assert s["win_rate_pct"] == 0.0
        assert s["avg_loss"] == 50.0
        assert s["profit_factor"] == 0.0

    def test_win_rate_calculation(self):
        j = TradeJournal()
        j.record(_trade(pnl=100.0))
        j.record(_trade(pnl=100.0))
        j.record(_trade(pnl=-50.0))
        j.record(_trade(pnl=-50.0))
        s = j.stats()
        assert s["total_trades"] == 4
        assert s["wins"] == 2
        assert s["losses"] == 2
        assert s["win_rate_pct"] == 50.0

    def test_profit_factor(self):
        j = TradeJournal()
        j.record(_trade(pnl=200.0))
        j.record(_trade(pnl=-100.0))
        s = j.stats()
        # gross_win=200, gross_loss=100 → PF=2.0
        assert abs(s["profit_factor"] - 2.0) < 1e-4

    def test_avg_win_avg_loss(self):
        j = TradeJournal()
        j.record(_trade(pnl=100.0))
        j.record(_trade(pnl=300.0))
        j.record(_trade(pnl=-50.0))
        j.record(_trade(pnl=-150.0))
        s = j.stats()
        assert abs(s["avg_win"] - 200.0) < 1e-4
        assert abs(s["avg_loss"] - 100.0) < 1e-4

    def test_total_pnl(self):
        j = TradeJournal()
        j.record(_trade(pnl=100.0))
        j.record(_trade(pnl=-30.0))
        s = j.stats()
        assert abs(s["total_pnl"] - 70.0) < 1e-6

    def test_total_trades_counter(self):
        j = TradeJournal()
        for _ in range(5):
            j.record(_trade())
        assert j.total_trades == 5


# ── Queries ───────────────────────────────────────────────────────────────────

class TestJournalQueries:
    def test_recent_returns_last_n(self):
        j = TradeJournal()
        for i in range(15):
            j.record(_trade(pnl=float(i)))
        recent = j.recent(5)
        assert len(recent) == 5
        # Last 5 pnl values: 10..14
        pnls = [float(t.pnl) for t in recent]
        assert pnls == [10.0, 11.0, 12.0, 13.0, 14.0]

    def test_recent_fewer_than_n(self):
        j = TradeJournal()
        j.record(_trade(pnl=1.0))
        assert len(j.recent(10)) == 1

    def test_for_symbol_filter(self):
        j = TradeJournal()
        j.record(_trade(symbol="BTC/USDT", pnl=100.0))
        j.record(_trade(symbol="ETH/USDT", pnl=-50.0))
        j.record(_trade(symbol="BTC/USDT", pnl=200.0))
        btc = j.for_symbol("BTC/USDT")
        assert len(btc) == 2
        assert all(t.symbol == "BTC/USDT" for t in btc)

    def test_trades_returns_copy(self):
        j = TradeJournal()
        j.record(_trade())
        trades = j.trades
        trades.clear()
        assert j.total_trades == 1

    def test_reason_stored(self):
        j = TradeJournal()
        j.record(_trade(reason="stop_loss"))
        assert j.trades[0].reason == "stop_loss"
