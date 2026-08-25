"""
SQLite-backed local storage for OHLCV bars.

Keyed by (exchange, symbol, timeframe, timestamp). Prices/volume are kept
as text (decimal string) so round-tripping through `Decimal` never loses
precision to float rounding.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.exchange.models import OHLCVBar


class OHLCVStorage:
    """Local OHLCV bar store backed by a SQLite file."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ohlcv (
                exchange  TEXT NOT NULL,
                symbol    TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                ts        TEXT NOT NULL,
                open      TEXT NOT NULL,
                high      TEXT NOT NULL,
                low       TEXT NOT NULL,
                close     TEXT NOT NULL,
                volume    TEXT NOT NULL,
                PRIMARY KEY (exchange, symbol, timeframe, ts)
            )
            """
        )
        self._conn.commit()

    def upsert(
        self, bars: list[OHLCVBar], exchange: str, symbol: str, timeframe: str
    ) -> int:
        """Insert/replace `bars`. Returns the number of bars passed in."""
        if not bars:
            return 0
        rows = [
            (
                exchange,
                symbol,
                timeframe,
                bar.timestamp.isoformat(),
                str(bar.open),
                str(bar.high),
                str(bar.low),
                str(bar.close),
                str(bar.volume),
            )
            for bar in bars
        ]
        self._conn.executemany(
            """
            INSERT INTO ohlcv (exchange, symbol, timeframe, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(exchange, symbol, timeframe, ts) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume
            """,
            rows,
        )
        self._conn.commit()
        return len(bars)

    def load(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[OHLCVBar]:
        """Return bars oldest-first. `limit` (if given) returns the most
        recent `limit` bars within [start, end], still oldest-first."""
        query = (
            "SELECT ts, open, high, low, close, volume FROM ohlcv "
            "WHERE exchange = ? AND symbol = ? AND timeframe = ?"
        )
        params: list = [exchange, symbol, timeframe]
        if start is not None:
            query += " AND ts >= ?"
            params.append(start.isoformat())
        if end is not None:
            query += " AND ts <= ?"
            params.append(end.isoformat())

        if limit is not None:
            query += " ORDER BY ts DESC LIMIT ?"
            params.append(limit)
        else:
            query += " ORDER BY ts ASC"

        rows = self._conn.execute(query, params).fetchall()
        if limit is not None:
            rows = list(reversed(rows))

        return [
            OHLCVBar(
                timestamp=datetime.fromisoformat(r[0]),
                open=Decimal(r[1]),
                high=Decimal(r[2]),
                low=Decimal(r[3]),
                close=Decimal(r[4]),
                volume=Decimal(r[5]),
            )
            for r in rows
        ]

    def count(self, exchange: str, symbol: str, timeframe: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE exchange = ? AND symbol = ? AND timeframe = ?",
            (exchange, symbol, timeframe),
        ).fetchone()
        return int(row[0]) if row else 0

    def available_series(self) -> list[dict]:
        """List every (exchange, symbol, timeframe) series with its bar count."""
        rows = self._conn.execute(
            "SELECT exchange, symbol, timeframe, COUNT(*) FROM ohlcv "
            "GROUP BY exchange, symbol, timeframe"
        ).fetchall()
        return [
            {"exchange": r[0], "symbol": r[1], "timeframe": r[2], "count": r[3]}
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()
