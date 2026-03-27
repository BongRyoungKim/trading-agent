"""Unit tests for SQLiteJournalStore and TradeJournal persistence."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.portfolio.journal import TradeJournal, TradeRecord
from src.portfolio.journal_store import SQLiteJournalStore


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
    exit_time: datetime | None = None,
) -> TradeRecord:
    return TradeRecord(
        symbol=symbol,
        side="buy",
        amount=Decimal(str(amount)),
        entry_price=Decimal(str(entry)),
        exit_price=Decimal(str(exit_)),
        entry_time=_T0,
        exit_time=exit_time or _T1,
        pnl=Decimal(str(pnl)),
        commission=Decimal("0.5"),
        reason=reason,
    )


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteJournalStore:
    return SQLiteJournalStore(db_path=tmp_path / "journal.db")


# ── Init ──────────────────────────────────────────────────────────────────────

class TestStoreInit:
    def test_creates_db_file(self, tmp_path: Path):
        db = tmp_path / "sub" / "journal.db"
        SQLiteJournalStore(db_path=db)
        assert db.exists()

    def test_empty_store_count_zero(self, store: SQLiteJournalStore):
        assert store.count() == 0

    def test_empty_store_load_all_empty(self, store: SQLiteJournalStore):
        assert store.load_all() == []


# ── Save ──────────────────────────────────────────────────────────────────────

class TestStoreSave:
    def test_save_increments_count(self, store: SQLiteJournalStore):
        store.save(_trade())
        assert store.count() == 1

    def test_save_multiple(self, store: SQLiteJournalStore):
        store.save(_trade(symbol="BTC/USDT"))
        store.save(_trade(symbol="ETH/USDT"))
        assert store.count() == 2

    def test_saved_trade_round_trips_decimal(self, store: SQLiteJournalStore):
        trade = _trade(pnl=123.456, entry=49999.99, exit_=51000.01)
        store.save(trade)
        loaded = store.load_all()
        assert loaded[0].pnl == Decimal("123.456")
        assert loaded[0].entry_price == Decimal("49999.99")

    def test_saved_trade_preserves_symbol(self, store: SQLiteJournalStore):
        store.save(_trade(symbol="ETH/USDT"))
        assert store.load_all()[0].symbol == "ETH/USDT"

    def test_saved_trade_preserves_reason(self, store: SQLiteJournalStore):
        store.save(_trade(reason="stop_loss"))
        assert store.load_all()[0].reason == "stop_loss"

    def test_saved_trade_preserves_commission(self, store: SQLiteJournalStore):
        store.save(_trade())
        assert store.load_all()[0].commission == Decimal("0.5")

    def test_saved_trade_timestamps_utc(self, store: SQLiteJournalStore):
        store.save(_trade())
        rec = store.load_all()[0]
        assert rec.entry_time.tzinfo is not None
        assert rec.exit_time.tzinfo is not None


# ── Load ──────────────────────────────────────────────────────────────────────

class TestStoreLoad:
    def test_load_all_oldest_first(self, store: SQLiteJournalStore):
        t1 = _trade(exit_time=datetime(2024, 1, 2, tzinfo=UTC))
        t2 = _trade(exit_time=datetime(2024, 1, 3, tzinfo=UTC))
        t3 = _trade(exit_time=datetime(2024, 1, 4, tzinfo=UTC))
        for t in [t2, t3, t1]:  # inserted out of order
            store.save(t)
        loaded = store.load_all()
        assert loaded[0].exit_time < loaded[1].exit_time < loaded[2].exit_time

    def test_load_since_filters_by_date(self, store: SQLiteJournalStore):
        store.save(_trade(exit_time=datetime(2024, 1, 1, tzinfo=UTC)))
        store.save(_trade(exit_time=datetime(2024, 1, 5, tzinfo=UTC)))
        store.save(_trade(exit_time=datetime(2024, 1, 10, tzinfo=UTC)))
        since = datetime(2024, 1, 5, tzinfo=UTC)
        loaded = store.load_since(since)
        assert len(loaded) == 2
        assert all(r.exit_time >= since for r in loaded)

    def test_load_since_empty_when_all_before(self, store: SQLiteJournalStore):
        store.save(_trade(exit_time=datetime(2024, 1, 1, tzinfo=UTC)))
        since = datetime(2024, 6, 1, tzinfo=UTC)
        assert store.load_since(since) == []

    def test_load_all_returns_all_fields(self, store: SQLiteJournalStore):
        store.save(_trade(symbol="BTC/USDT", pnl=50.0, amount=0.02, reason="take_profit"))
        rec = store.load_all()[0]
        assert rec.symbol == "BTC/USDT"
        assert rec.pnl == Decimal("50.0")
        assert rec.amount == Decimal("0.02")
        assert rec.reason == "take_profit"
        assert rec.side == "buy"


# ── Persistence across instances ──────────────────────────────────────────────

class TestStorePersistence:
    def test_trades_survive_store_reload(self, tmp_path: Path):
        db_path = tmp_path / "journal.db"
        store1 = SQLiteJournalStore(db_path=db_path)
        store1.save(_trade(pnl=200.0))
        store1.save(_trade(pnl=-50.0))

        # Create new instance pointing to same file
        store2 = SQLiteJournalStore(db_path=db_path)
        loaded = store2.load_all()
        assert len(loaded) == 2
        assert {float(r.pnl) for r in loaded} == {200.0, -50.0}


# ── TradeJournal integration ──────────────────────────────────────────────────

class TestJournalWithStore:
    def test_record_persists_to_store(self, store: SQLiteJournalStore):
        journal = TradeJournal(store=store)
        journal.record(_trade(pnl=100.0))
        assert store.count() == 1

    def test_from_store_loads_history(self, tmp_path: Path):
        db_path = tmp_path / "j.db"
        store = SQLiteJournalStore(db_path=db_path)
        store.save(_trade(pnl=100.0))
        store.save(_trade(pnl=-30.0))

        # Load into new journal
        journal = TradeJournal.from_store(store)
        assert journal.total_trades == 2

    def test_from_store_stats_include_history(self, tmp_path: Path):
        db_path = tmp_path / "j.db"
        store = SQLiteJournalStore(db_path=db_path)
        store.save(_trade(pnl=100.0))
        store.save(_trade(pnl=-50.0))

        journal = TradeJournal.from_store(store)
        stats = journal.stats()
        assert stats["total_trades"] == 2
        assert stats["wins"] == 1
        assert stats["win_rate_pct"] == 50.0

    def test_new_records_also_persisted(self, tmp_path: Path):
        db_path = tmp_path / "j.db"
        store1 = SQLiteJournalStore(db_path=db_path)
        journal = TradeJournal.from_store(store1)
        journal.record(_trade(pnl=75.0))

        # Reload from disk
        store2 = SQLiteJournalStore(db_path=db_path)
        journal2 = TradeJournal.from_store(store2)
        assert journal2.total_trades == 1
        assert float(journal2.trades[0].pnl) == 75.0

    def test_without_store_no_persistence(self):
        journal = TradeJournal()  # no store
        journal.record(_trade())
        assert journal.total_trades == 1
        # No error — just in-memory

    def test_win_rate_survives_restart(self, tmp_path: Path):
        """Simulate engine restart: win_rate is correct after reload."""
        db_path = tmp_path / "j.db"

        # Session 1: 3 wins, 1 loss
        store1 = SQLiteJournalStore(db_path=db_path)
        j1 = TradeJournal.from_store(store1)
        j1.record(_trade(pnl=100.0))
        j1.record(_trade(pnl=200.0))
        j1.record(_trade(pnl=150.0))
        j1.record(_trade(pnl=-80.0))

        # Session 2: reload + 1 more win
        store2 = SQLiteJournalStore(db_path=db_path)
        j2 = TradeJournal.from_store(store2)
        j2.record(_trade(pnl=50.0))

        stats = j2.stats()
        assert stats["total_trades"] == 5
        assert stats["wins"] == 4
        assert stats["win_rate_pct"] == 80.0
