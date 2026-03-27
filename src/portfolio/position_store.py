"""
SQLite-backed persistence for open positions in PortfolioTracker.

On engine restart, open positions are restored so that stop-loss checks
and position tracking continue seamlessly. A position row is inserted on
open and deleted on close — at any point the table reflects reality.

Usage:
    store = SQLitePositionStore()
    portfolio = PortfolioTracker.from_store(initial_cash, store)
    # open / close work as normal; the store stays in sync automatically
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Generator, Literal

from loguru import logger

from src.portfolio.models import Position


_DEFAULT_DB_PATH = Path("data/positions.db")


class SQLitePositionStore:
    """
    Persist open positions to SQLite.

    One row per open position keyed by ``symbol``. Rows are written on
    ``save()`` (upsert) and removed on ``delete()``.  ``load_all()``
    restores the full open-position dict on startup.
    """

    def __init__(self, db_path: Path | str = _DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Internal ──────────────────────────────────────────────────────────────

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    symbol              TEXT PRIMARY KEY,
                    side                TEXT    NOT NULL,
                    amount              TEXT    NOT NULL,
                    entry_price         TEXT    NOT NULL,
                    entry_time          REAL    NOT NULL,
                    stop_loss           TEXT,
                    take_profit         TEXT,
                    trailing_stop_pct   TEXT
                )
            """)
            # Migration for existing DBs that lack the new column
            try:
                conn.execute(
                    "ALTER TABLE positions ADD COLUMN trailing_stop_pct TEXT"
                )
            except Exception:  # noqa: BLE001 — column already exists
                pass
        logger.debug("SQLitePositionStore initialized", db=str(self._db_path))

    # ── Write ─────────────────────────────────────────────────────────────────

    def save(self, position: Position) -> None:
        """Insert or replace a position row."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO positions
                    (symbol, side, amount, entry_price, entry_time,
                     stop_loss, take_profit, trailing_stop_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position.symbol,
                    position.side,
                    str(position.amount),
                    str(position.entry_price),
                    position.entry_time.timestamp(),
                    str(position.stop_loss) if position.stop_loss is not None else None,
                    str(position.take_profit) if position.take_profit is not None else None,
                    str(position.trailing_stop_pct)
                    if position.trailing_stop_pct is not None
                    else None,
                ),
            )

    def delete(self, symbol: str) -> None:
        """Remove a position row (called on close)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))

    def delete_all(self) -> None:
        """Remove all position rows (e.g. for paper-trading resets)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM positions")

    # ── Read ──────────────────────────────────────────────────────────────────

    def load_all(self) -> dict[str, Position]:
        """Load all open positions, keyed by symbol."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM positions").fetchall()
        return {row["symbol"]: self._row_to_position(row) for row in rows}

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]

    # ── Conversion ────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_position(row: sqlite3.Row) -> Position:
        keys = row.keys()
        trailing = (
            Decimal(row["trailing_stop_pct"])
            if "trailing_stop_pct" in keys and row["trailing_stop_pct"] is not None
            else None
        )
        return Position(
            symbol=row["symbol"],
            side=row["side"],  # type: ignore[arg-type]
            amount=Decimal(row["amount"]),
            entry_price=Decimal(row["entry_price"]),
            entry_time=datetime.fromtimestamp(row["entry_time"], tz=UTC),
            stop_loss=Decimal(row["stop_loss"]) if row["stop_loss"] is not None else None,
            take_profit=Decimal(row["take_profit"]) if row["take_profit"] is not None else None,
            trailing_stop_pct=trailing,
        )
