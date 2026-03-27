"""Unit tests for src/data/storage.py"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.data.storage import OHLCVStorage
from src.exchange.models import OHLCVBar


def _make_bar(
    ts: datetime,
    open_: float = 50000.0,
    high: float = 51000.0,
    low: float = 49000.0,
    close: float = 50500.0,
    volume: float = 100.0,
) -> OHLCVBar:
    return OHLCVBar(
        timestamp=ts,
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
    )


@pytest.fixture()
def storage(tmp_path: Path) -> OHLCVStorage:
    return OHLCVStorage(db_path=tmp_path / "test.db")


@pytest.fixture()
def sample_bars() -> list[OHLCVBar]:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [_make_bar(datetime(2024, 1, 1, i, tzinfo=UTC), close=50000.0 + i * 100) for i in range(5)]


class TestUpsert:
    def test_upsert_returns_count(self, storage: OHLCVStorage, sample_bars: list[OHLCVBar]) -> None:
        count = storage.upsert(sample_bars, "binance", "BTC/USDT", "1h")
        assert count == 5

    def test_upsert_empty_list_returns_zero(self, storage: OHLCVStorage) -> None:
        count = storage.upsert([], "binance", "BTC/USDT", "1h")
        assert count == 0

    def test_upsert_deduplicates(self, storage: OHLCVStorage, sample_bars: list[OHLCVBar]) -> None:
        storage.upsert(sample_bars, "binance", "BTC/USDT", "1h")
        storage.upsert(sample_bars, "binance", "BTC/USDT", "1h")  # same bars
        assert storage.count("binance", "BTC/USDT", "1h") == 5

    def test_upsert_replaces_existing(self, storage: OHLCVStorage) -> None:
        ts = datetime(2024, 1, 1, tzinfo=UTC)
        bar_v1 = _make_bar(ts, close=50000.0)
        bar_v2 = _make_bar(ts, close=99999.0)

        storage.upsert([bar_v1], "binance", "BTC/USDT", "1h")
        storage.upsert([bar_v2], "binance", "BTC/USDT", "1h")

        loaded = storage.load("binance", "BTC/USDT", "1h")
        assert len(loaded) == 1
        assert loaded[0].close == Decimal("99999.0")


class TestLoad:
    def test_load_returns_bars_oldest_first(
        self, storage: OHLCVStorage, sample_bars: list[OHLCVBar]
    ) -> None:
        storage.upsert(sample_bars, "binance", "BTC/USDT", "1h")
        loaded = storage.load("binance", "BTC/USDT", "1h")
        assert len(loaded) == 5
        for i in range(len(loaded) - 1):
            assert loaded[i].timestamp < loaded[i + 1].timestamp

    def test_load_empty_returns_empty_list(self, storage: OHLCVStorage) -> None:
        result = storage.load("binance", "BTC/USDT", "1h")
        assert result == []

    def test_load_with_start_filter(
        self, storage: OHLCVStorage, sample_bars: list[OHLCVBar]
    ) -> None:
        storage.upsert(sample_bars, "binance", "BTC/USDT", "1h")
        start = datetime(2024, 1, 1, 2, tzinfo=UTC)
        loaded = storage.load("binance", "BTC/USDT", "1h", start=start)
        assert all(b.timestamp >= start for b in loaded)
        assert len(loaded) == 3  # hours 2, 3, 4

    def test_load_with_end_filter(
        self, storage: OHLCVStorage, sample_bars: list[OHLCVBar]
    ) -> None:
        storage.upsert(sample_bars, "binance", "BTC/USDT", "1h")
        end = datetime(2024, 1, 1, 2, tzinfo=UTC)
        loaded = storage.load("binance", "BTC/USDT", "1h", end=end)
        assert all(b.timestamp <= end for b in loaded)
        assert len(loaded) == 3  # hours 0, 1, 2

    def test_load_with_limit(
        self, storage: OHLCVStorage, sample_bars: list[OHLCVBar]
    ) -> None:
        storage.upsert(sample_bars, "binance", "BTC/USDT", "1h")
        loaded = storage.load("binance", "BTC/USDT", "1h", limit=2)
        assert len(loaded) == 2

    def test_load_different_exchanges_isolated(
        self, storage: OHLCVStorage, sample_bars: list[OHLCVBar]
    ) -> None:
        storage.upsert(sample_bars, "binance", "BTC/USDT", "1h")
        storage.upsert(sample_bars, "upbit", "BTC/KRW", "1h")
        assert storage.count("binance", "BTC/USDT", "1h") == 5
        assert storage.count("upbit", "BTC/KRW", "1h") == 5
        assert storage.count("binance", "BTC/KRW", "1h") == 0


class TestMeta:
    def test_count_returns_correct_number(
        self, storage: OHLCVStorage, sample_bars: list[OHLCVBar]
    ) -> None:
        storage.upsert(sample_bars, "binance", "BTC/USDT", "1h")
        assert storage.count("binance", "BTC/USDT", "1h") == 5

    def test_count_missing_series_returns_zero(self, storage: OHLCVStorage) -> None:
        assert storage.count("binance", "DOGE/USDT", "1m") == 0

    def test_available_series(
        self, storage: OHLCVStorage, sample_bars: list[OHLCVBar]
    ) -> None:
        storage.upsert(sample_bars, "binance", "BTC/USDT", "1h")
        storage.upsert(sample_bars[:2], "binance", "ETH/USDT", "1d")
        series = storage.available_series()
        assert len(series) == 2
        symbols = {s["symbol"] for s in series}
        assert "BTC/USDT" in symbols
        assert "ETH/USDT" in symbols

    def test_available_series_empty_db(self, storage: OHLCVStorage) -> None:
        assert storage.available_series() == []
