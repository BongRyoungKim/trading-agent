"""
SQLite-backed persistence for TradeJournal.

Allows completed trades to survive engine restarts. The journal is reloaded
from the DB on startup so win_rate and daily reports remain accurate.

Usage:
    store = SQLiteJournalStore()
    journal = TradeJournal(store=store)
    # journal.record() now auto-persists every trade
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Generator, Literal

from loguru import logger

from src.portfolio.journal import TradeRecord


_DEFAULT_DB_PATH = Path("data/journal.db")


class SQLiteJournalStore:
    """
    Persist and reload TradeRecord objects in a SQLite database.

    One row per completed trade. Duplicate trades (same symbol + exit_time)
    are silently ignored on insert to preserve idempotency.
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
                CREATE TABLE IF NOT EXISTS trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol      TEXT    NOT NULL,
                    side        TEXT    NOT NULL,
                    amount      TEXT    NOT NULL,
                    entry_price TEXT    NOT NULL,
                    exit_price  TEXT    NOT NULL,
                    entry_time  REAL    NOT NULL,
                    exit_time   REAL    NOT NULL,
                    pnl         TEXT    NOT NULL,
                    commission  TEXT    NOT NULL,
                    reason      TEXT    NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_symbol
                ON trades (symbol)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_exit_time
                ON trades (exit_time)
            """)
            # Unique constraint: prevents duplicate records for the same trade.
            # Uses IF NOT EXISTS so existing DBs without the index are upgraded safely.
            # Will silently skip creation if duplicate rows already exist (log warning).
            try:
                conn.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_unique
                    ON trades (symbol, exit_time)
                """)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"Could not create unique index on trades (existing duplicates?): {exc}"
                )
        logger.debug("SQLiteJournalStore initialized", db=str(self._db_path))

    # ── Write ─────────────────────────────────────────────────────────────────

    def save(self, trade: TradeRecord) -> None:
        """Persist a TradeRecord. Silently ignores duplicate (symbol, exit_time)."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO trades
                    (symbol, side, amount, entry_price, exit_price,
                     entry_time, exit_time, pnl, commission, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.symbol,
                    trade.side,
                    str(trade.amount),
                    str(trade.entry_price),
                    str(trade.exit_price),
                    trade.entry_time.timestamp(),
                    trade.exit_time.timestamp(),
                    str(trade.pnl),
                    str(trade.commission),
                    trade.reason,
                ),
            )

    # ── Read ──────────────────────────────────────────────────────────────────

    def load_all(self) -> list[TradeRecord]:
        """Load all persisted trades, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY exit_time ASC, id ASC"
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def load_since(self, since: datetime) -> list[TradeRecord]:
        """Load trades with exit_time >= since."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE exit_time >= ? ORDER BY exit_time ASC, id ASC",
                (since.timestamp(),),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def count(self) -> int:
        """Total number of persisted trades."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    # ── Internal conversion ───────────────────────────────────────────────────

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> TradeRecord:
        return TradeRecord(
            symbol=row["symbol"],
            side=row["side"],  # type: ignore[arg-type]
            amount=Decimal(row["amount"]),
            entry_price=Decimal(row["entry_price"]),
            exit_price=Decimal(row["exit_price"]),
            entry_time=datetime.fromtimestamp(row["entry_time"], tz=UTC),
            exit_time=datetime.fromtimestamp(row["exit_time"], tz=UTC),
            pnl=Decimal(row["pnl"]),
            commission=Decimal(row["commission"]),
            reason=row["reason"],
        )
