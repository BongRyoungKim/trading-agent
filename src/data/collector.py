"""
Fetches OHLCV bars from an exchange client and persists them via
`OHLCVStorage`.
"""
from __future__ import annotations

from src.exchange.base import BaseExchangeClient
from src.exchange.models import OHLCVBar
from src.data.storage import OHLCVStorage


class DataCollector:
    """Pulls OHLCV bars from an exchange client and stores them locally."""

    def __init__(self, client: BaseExchangeClient, storage: OHLCVStorage) -> None:
        self._client = client
        self._storage = storage

    def fetch_and_store(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> list[OHLCVBar]:
        """Fetch the latest `limit` bars and upsert them into storage."""
        bars = self._client.get_ohlcv(symbol, timeframe, limit)
        self._storage.upsert(bars, self._client.exchange_id, symbol, timeframe)
        return bars

    def download_history(
        self,
        symbol: str,
        timeframe: str,
        days_back: int,
        batch_size: int = 500,
    ) -> int:
        """Backfill historical bars.

        Note: `BaseExchangeClient.get_ohlcv` has no time-cursor parameter
        (no `since`/`before`), so this cannot page arbitrarily far into
        the past — each call returns the same "latest N" bars. `days_back`
        is therefore used as an upper bound on the number of fetch
        attempts rather than a guaranteed history depth; the loop always
        stops early once a call returns no bars, or fewer than
        `batch_size` (a sign we've reached the start of history).
        """
        total = 0
        max_batches = max(1, days_back)
        for _ in range(max_batches):
            bars = self._client.get_ohlcv(symbol, timeframe, batch_size)
            if not bars:
                break
            total += self._storage.upsert(
                bars, self._client.exchange_id, symbol, timeframe
            )
            if len(bars) < batch_size:
                break
        return total

    def get_latest_bar(
        self, symbol: str, timeframe: str = "1h"
    ) -> OHLCVBar | None:
        """Fetch (and store) the single most recent bar, or None if unavailable."""
        bars = self._client.get_ohlcv(symbol, timeframe, 1)
        if not bars:
            return None
        self._storage.upsert(bars, self._client.exchange_id, symbol, timeframe)
        return bars[-1]
