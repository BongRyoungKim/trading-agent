"""Unit tests for SQLitePositionStore and PortfolioTracker persistence."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.portfolio.models import Position
from src.portfolio.position_store import SQLitePositionStore
from src.portfolio.tracker import PortfolioTracker


# ── Helpers ───────────────────────────────────────────────────────────────────

_NOW = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


def _pos(
    symbol: str = "BTC/USDT",
    side: str = "buy",
    amount: float = 0.01,
    entry: float = 50000.0,
    stop_loss: float | None = None,
    take_profit: float | None = None,
) -> Position:
    return Position(
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        amount=Decimal(str(amount)),
        entry_price=Decimal(str(entry)),
        entry_time=_NOW,
        stop_loss=Decimal(str(stop_loss)) if stop_loss is not None else None,
        take_profit=Decimal(str(take_profit)) if take_profit is not None else None,
    )


@pytest.fixture()
def store(tmp_path: Path) -> SQLitePositionStore:
    return SQLitePositionStore(db_path=tmp_path / "positions.db")


# ── Init ──────────────────────────────────────────────────────────────────────

class TestStoreInit:
    def test_creates_db_file(self, tmp_path: Path):
        db = tmp_path / "sub" / "positions.db"
        SQLitePositionStore(db_path=db)
        assert db.exists()

    def test_empty_store_count_zero(self, store):
        assert store.count() == 0

    def test_empty_store_load_all_empty(self, store):
        assert store.load_all() == {}


# ── Save ──────────────────────────────────────────────────────────────────────

class TestStoreSave:
    def test_save_increments_count(self, store):
        store.save(_pos())
        assert store.count() == 1

    def test_save_multiple_symbols(self, store):
        store.save(_pos("BTC/USDT"))
        store.save(_pos("ETH/USDT"))
        assert store.count() == 2

    def test_save_upserts_same_symbol(self, store):
        store.save(_pos("BTC/USDT", entry=50000.0))
        store.save(_pos("BTC/USDT", entry=51000.0))  # update
        assert store.count() == 1
        loaded = store.load_all()
        assert loaded["BTC/USDT"].entry_price == Decimal("51000.0")

    def test_save_with_stop_loss(self, store):
        store.save(_pos(stop_loss=48000.0))
        pos = store.load_all()["BTC/USDT"]
        assert pos.stop_loss == Decimal("48000.0")

    def test_save_with_take_profit(self, store):
        store.save(_pos(take_profit=55000.0))
        pos = store.load_all()["BTC/USDT"]
        assert pos.take_profit == Decimal("55000.0")

    def test_save_without_sl_tp_stores_none(self, store):
        store.save(_pos(stop_loss=None, take_profit=None))
        pos = store.load_all()["BTC/USDT"]
        assert pos.stop_loss is None
        assert pos.take_profit is None

    def test_save_preserves_decimal_precision(self, store):
        store.save(_pos(amount=0.00123456, entry=49999.99))
        pos = store.load_all()["BTC/USDT"]
        assert pos.amount == Decimal("0.00123456")
        assert pos.entry_price == Decimal("49999.99")


# ── Delete ────────────────────────────────────────────────────────────────────

class TestStoreDelete:
    def test_delete_removes_position(self, store):
        store.save(_pos("BTC/USDT"))
        store.delete("BTC/USDT")
        assert store.count() == 0

    def test_delete_only_removes_target(self, store):
        store.save(_pos("BTC/USDT"))
        store.save(_pos("ETH/USDT"))
        store.delete("BTC/USDT")
        assert store.count() == 1
        assert "ETH/USDT" in store.load_all()

    def test_delete_nonexistent_symbol_no_error(self, store):
        store.delete("XYZ/USDT")  # should not raise

    def test_delete_all_clears_table(self, store):
        store.save(_pos("BTC/USDT"))
        store.save(_pos("ETH/USDT"))
        store.delete_all()
        assert store.count() == 0


# ── Load ──────────────────────────────────────────────────────────────────────

class TestStoreLoad:
    def test_load_all_returns_keyed_by_symbol(self, store):
        store.save(_pos("BTC/USDT"))
        store.save(_pos("ETH/USDT"))
        loaded = store.load_all()
        assert set(loaded.keys()) == {"BTC/USDT", "ETH/USDT"}

    def test_loaded_position_utc_aware(self, store):
        store.save(_pos())
        pos = store.load_all()["BTC/USDT"]
        assert pos.entry_time.tzinfo is not None

    def test_round_trip_all_fields(self, store):
        original = _pos(symbol="ETH/USDT", side="sell", amount=0.5,
                        entry=3000.0, stop_loss=2800.0, take_profit=3200.0)
        store.save(original)
        loaded = store.load_all()["ETH/USDT"]
        assert loaded.symbol == "ETH/USDT"
        assert loaded.side == "sell"
        assert loaded.amount == Decimal("0.5")
        assert loaded.entry_price == Decimal("3000.0")
        assert loaded.stop_loss == Decimal("2800.0")
        assert loaded.take_profit == Decimal("3200.0")


# ── Persistence across instances ──────────────────────────────────────────────

class TestStorePersistence:
    def test_positions_survive_store_reload(self, tmp_path):
        db = tmp_path / "pos.db"
        s1 = SQLitePositionStore(db_path=db)
        s1.save(_pos("BTC/USDT", entry=50000.0))
        s1.save(_pos("ETH/USDT", entry=3000.0))

        s2 = SQLitePositionStore(db_path=db)
        loaded = s2.load_all()
        assert len(loaded) == 2
        assert "BTC/USDT" in loaded


# ── PortfolioTracker integration ──────────────────────────────────────────────

class TestTrackerWithStore:
    def test_open_position_saves_to_store(self, store):
        tracker = PortfolioTracker(initial_cash=Decimal("10000"), store=store)
        tracker.open_position("BTC/USDT", "buy", Decimal("0.01"), Decimal("50000"))
        assert store.count() == 1

    def test_close_position_deletes_from_store(self, store):
        tracker = PortfolioTracker(initial_cash=Decimal("10000"), store=store)
        tracker.open_position("BTC/USDT", "buy", Decimal("0.01"), Decimal("50000"))
        tracker.close_position("BTC/USDT", Decimal("51000"))
        assert store.count() == 0

    def test_open_multiple_positions(self, store):
        tracker = PortfolioTracker(initial_cash=Decimal("100000"), store=store)
        tracker.open_position("BTC/USDT", "buy", Decimal("0.01"), Decimal("50000"))
        tracker.open_position("ETH/USDT", "buy", Decimal("1.0"), Decimal("3000"))
        assert store.count() == 2

    def test_from_store_restores_positions(self, tmp_path):
        db = tmp_path / "pos.db"
        s1 = SQLitePositionStore(db_path=db)

        # Session 1: open two positions
        t1 = PortfolioTracker(initial_cash=Decimal("100000"), store=s1)
        t1.open_position("BTC/USDT", "buy", Decimal("0.01"), Decimal("50000"))
        t1.open_position("ETH/USDT", "buy", Decimal("1.0"), Decimal("3000"))

        # Session 2: restore
        s2 = SQLitePositionStore(db_path=db)
        t2 = PortfolioTracker.from_store(initial_cash=Decimal("100000"), store=s2)

        assert t2.has_position("BTC/USDT")
        assert t2.has_position("ETH/USDT")
        assert t2.open_symbols() == ["BTC/USDT", "ETH/USDT"] or set(t2.open_symbols()) == {"BTC/USDT", "ETH/USDT"}

    def test_from_store_cash_adjusted_for_restored_positions(self, tmp_path):
        db = tmp_path / "pos.db"
        s1 = SQLitePositionStore(db_path=db)
        t1 = PortfolioTracker(initial_cash=Decimal("100000"), store=s1)
        # Open BTC position: cost = 50000 * 0.01 = 500
        t1.open_position("BTC/USDT", "buy", Decimal("0.01"), Decimal("50000"))

        # Restore: cash should be reduced by 500
        s2 = SQLitePositionStore(db_path=db)
        t2 = PortfolioTracker.from_store(initial_cash=Decimal("100000"), store=s2)
        assert t2.cash == Decimal("99500")

    def test_from_store_no_positions_full_cash(self, tmp_path):
        db = tmp_path / "pos.db"
        store = SQLitePositionStore(db_path=db)
        tracker = PortfolioTracker.from_store(initial_cash=Decimal("10000"), store=store)
        assert tracker.cash == Decimal("10000")
        assert tracker.open_symbols() == []

    def test_stop_loss_preserved_after_restore(self, tmp_path):
        db = tmp_path / "pos.db"
        s1 = SQLitePositionStore(db_path=db)
        t1 = PortfolioTracker(initial_cash=Decimal("10000"), store=s1)
        t1.open_position(
            "BTC/USDT", "buy", Decimal("0.01"), Decimal("50000"),
            stop_loss=Decimal("45000"),
        )

        s2 = SQLitePositionStore(db_path=db)
        t2 = PortfolioTracker.from_store(initial_cash=Decimal("10000"), store=s2)
        pos = t2.get_position("BTC/USDT")
        assert pos is not None
        assert pos.stop_loss == Decimal("45000")

    def test_tracker_without_store_no_persistence(self):
        tracker = PortfolioTracker(initial_cash=Decimal("10000"))  # no store
        tracker.open_position("BTC/USDT", "buy", Decimal("0.01"), Decimal("50000"))
        # No error — in-memory only
        assert tracker.has_position("BTC/USDT")
