"""Unit tests for src/data/collector.py"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.data.collector import DataCollector
from src.data.storage import OHLCVStorage
from src.exchange.models import OHLCVBar


def _make_bars(n: int = 5) -> list[OHLCVBar]:
    return [
        OHLCVBar(
            timestamp=datetime(2024, 1, 1, i, tzinfo=UTC),
            open=Decimal("50000"),
            high=Decimal("51000"),
            low=Decimal("49000"),
            close=Decimal("50500"),
            volume=Decimal("100"),
        )
        for i in range(n)
    ]


@pytest.fixture()
def mock_client() -> MagicMock:
    client = MagicMock()
    client.exchange_id = "binance"
    client.get_ohlcv.return_value = _make_bars(5)
    return client


@pytest.fixture()
def storage(tmp_path: Path) -> OHLCVStorage:
    return OHLCVStorage(db_path=tmp_path / "test.db")


@pytest.fixture()
def collector(mock_client: MagicMock, storage: OHLCVStorage) -> DataCollector:
    return DataCollector(mock_client, storage)


class TestFetchAndStore:
    def test_returns_fetched_bars(self, collector: DataCollector) -> None:
        bars = collector.fetch_and_store("BTC/USDT", "1h", limit=5)
        assert len(bars) == 5

    def test_stores_bars_in_storage(
        self, collector: DataCollector, storage: OHLCVStorage
    ) -> None:
        collector.fetch_and_store("BTC/USDT", "1h", limit=5)
        assert storage.count("binance", "BTC/USDT", "1h") == 5

    def test_calls_client_with_correct_args(
        self, collector: DataCollector, mock_client: MagicMock
    ) -> None:
        collector.fetch_and_store("ETH/USDT", "4h", limit=100)
        mock_client.get_ohlcv.assert_called_once_with("ETH/USDT", "4h", 100)


class TestDownloadHistory:
    def test_returns_total_stored(self, collector: DataCollector) -> None:
        total = collector.download_history("BTC/USDT", "1d", days_back=1, batch_size=5)
        assert total > 0

    def test_stops_when_no_bars_returned(
        self, mock_client: MagicMock, storage: OHLCVStorage
    ) -> None:
        mock_client.get_ohlcv.return_value = []
        collector = DataCollector(mock_client, storage)
        total = collector.download_history("BTC/USDT", "1h", days_back=30)
        assert total == 0

    def test_stops_when_partial_batch_returned(
        self, mock_client: MagicMock, storage: OHLCVStorage
    ) -> None:
        # Returns only 3 bars when 500 requested → reached beginning of history
        mock_client.get_ohlcv.return_value = _make_bars(3)
        collector = DataCollector(mock_client, storage)
        total = collector.download_history("BTC/USDT", "1h", days_back=30, batch_size=500)
        assert total == 3
        assert mock_client.get_ohlcv.call_count == 1


class TestGetLatestBar:
    def test_returns_bar_when_available(self, collector: DataCollector) -> None:
        bar = collector.get_latest_bar("BTC/USDT", "1h")
        assert bar is not None

    def test_returns_none_when_no_bars(
        self, mock_client: MagicMock, storage: OHLCVStorage
    ) -> None:
        mock_client.get_ohlcv.return_value = []
        collector = DataCollector(mock_client, storage)
        bar = collector.get_latest_bar("BTC/USDT")
        assert bar is None
