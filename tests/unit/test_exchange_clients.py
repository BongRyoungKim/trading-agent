"""
Unit tests for BinanceClient and UpbitClient.
All ccxt calls are mocked - no real network requests.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.exchange.binance import BinanceClient
from src.exchange.models import OrderStatus, OrderType
from src.exchange.upbit import UpbitClient
from src.utils.exceptions import (
    AuthenticationError,
    InsufficientFundsError,
    OrderNotFoundError,
    RateLimitError,
)


def _make_raw_order(
    order_id: str = "123",
    symbol: str = "BTC/USDT",
    side: str = "buy",
    status: str = "open",
) -> dict:
    return {
        "id": order_id,
        "symbol": symbol,
        "side": side,
        "type": "limit",
        "amount": 0.01,
        "price": 50000.0,
        "filled": 0.0,
        "status": status,
        "timestamp": 1700000000000,
    }


def _make_raw_ticker(symbol: str = "BTC/USDT") -> dict:
    return {
        "symbol": symbol,
        "bid": 49900.0,
        "ask": 50100.0,
        "last": 50000.0,
        "baseVolume": 500.0,
        "timestamp": 1700000000000,
    }


def _make_raw_ohlcv() -> list:
    return [[1700000000000, 50000.0, 51000.0, 49000.0, 50500.0, 100.0]]


@pytest.fixture()
def mock_ccxt_binance():
    """Return a MagicMock that mimics ccxt.binance."""
    mock_exchange = MagicMock()
    mock_exchange.fetch_balance.return_value = {
        "BTC": {"free": 1.0, "used": 0.0, "total": 1.0},
        "USDT": {"free": 10000.0, "used": 0.0, "total": 10000.0},
        "info": {},
    }
    mock_exchange.create_order.return_value = _make_raw_order()
    mock_exchange.cancel_order.return_value = {"id": "123", "status": "canceled"}
    mock_exchange.fetch_ticker.return_value = _make_raw_ticker()
    mock_exchange.fetch_ohlcv.return_value = _make_raw_ohlcv()

    with patch("src.exchange.binance._import_ccxt") as mock_import:
        mock_ccxt = MagicMock()
        mock_ccxt.binance.return_value = mock_exchange
        mock_ccxt.AuthenticationError = Exception
        mock_ccxt.RateLimitExceeded = Exception
        mock_ccxt.InsufficientFunds = Exception
        mock_ccxt.OrderNotFound = Exception
        mock_ccxt.NetworkError = Exception
        mock_ccxt.ExchangeNotAvailable = Exception
        mock_import.return_value = mock_ccxt
        yield mock_exchange


@pytest.fixture()
def binance_client(mock_ccxt_binance: MagicMock) -> BinanceClient:
    return BinanceClient(api_key="test_key", secret_key="test_secret")


class TestBinanceClientGetBalance:
    def test_returns_non_zero_balances(self, binance_client: BinanceClient) -> None:
        balances = binance_client.get_balance()
        assert "BTC" in balances
        assert "USDT" in balances
        assert balances["BTC"].total == Decimal("1.0")

    def test_excludes_zero_balances(self, binance_client: BinanceClient) -> None:
        balances = binance_client.get_balance()
        # "info" key has non-dict value, should be excluded
        assert "info" not in balances


class TestBinanceClientPlaceOrder:
    def test_place_limit_order(self, binance_client: BinanceClient) -> None:
        order = binance_client.place_order("BTC/USDT", "buy", 0.01, price=50000.0)
        assert order.id == "123"
        assert order.side == "buy"
        assert order.order_type == OrderType.LIMIT
        assert order.status == OrderStatus.OPEN

    def test_place_market_order(self, binance_client: BinanceClient, mock_ccxt_binance: MagicMock) -> None:
        mock_ccxt_binance.create_order.return_value = {
            **_make_raw_order(),
            "type": "market",
            "price": 0.0,
        }
        order = binance_client.place_order("BTC/USDT", "buy", 0.01)
        mock_ccxt_binance.create_order.assert_called_once()
        call_kwargs = mock_ccxt_binance.create_order.call_args
        assert call_kwargs.kwargs.get("type") == "market" or call_kwargs[1].get("type") == "market"


class TestBinanceClientCancelOrder:
    def test_cancel_existing_order(self, binance_client: BinanceClient) -> None:
        result = binance_client.cancel_order("123", "BTC/USDT")
        assert result is True

    def test_cancel_not_found_returns_false(self) -> None:
        class FakeOrderNotFound(Exception):
            pass

        mock_exchange = MagicMock()
        mock_exchange.cancel_order.side_effect = FakeOrderNotFound("not found")

        with patch("src.exchange.binance._import_ccxt") as mock_import:
            mock_ccxt = MagicMock()
            mock_ccxt.OrderNotFound = FakeOrderNotFound
            mock_ccxt.AuthenticationError = type("AuthErr", (Exception,), {})
            mock_ccxt.RateLimitExceeded = type("RateLimit", (Exception,), {})
            mock_ccxt.InsufficientFunds = type("InsFunds", (Exception,), {})
            mock_ccxt.NetworkError = type("NetErr", (Exception,), {})
            mock_ccxt.ExchangeNotAvailable = type("ExchNA", (Exception,), {})
            mock_ccxt.binance.return_value = mock_exchange
            mock_import.return_value = mock_ccxt

            client = BinanceClient("k", "s")
            result = client.cancel_order("999", "BTC/USDT")
            assert result is False


class TestBinanceClientGetOHLCV:
    def test_returns_ohlcv_bars(self, binance_client: BinanceClient) -> None:
        bars = binance_client.get_ohlcv("BTC/USDT", "1h", 1)
        assert len(bars) == 1
        assert bars[0].close == Decimal("50500.0")

    def test_get_ohlcv_dataframe_columns(self, binance_client: BinanceClient) -> None:
        df = binance_client.get_ohlcv_dataframe("BTC/USDT", "1h", 1)
        assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]


class TestBinanceClientGetTicker:
    def test_returns_ticker(self, binance_client: BinanceClient) -> None:
        ticker = binance_client.get_ticker("BTC/USDT")
        assert ticker.symbol == "BTC/USDT"
        assert ticker.last == Decimal("50000.0")


class TestExchangeId:
    def test_binance_exchange_id(self, binance_client: BinanceClient) -> None:
        assert binance_client.exchange_id == "binance"


# ── Upbit Client Tests ─────────────────────────────────────────────────────────

@pytest.fixture()
def mock_ccxt_upbit():
    """Return a MagicMock that mimics ccxt.upbit, patching at the upbit module level."""
    mock_exchange = MagicMock()
    mock_exchange.fetch_balance.return_value = {
        "BTC": {"free": 0.5, "used": 0.0, "total": 0.5},
        "KRW": {"free": 1000000.0, "used": 0.0, "total": 1000000.0},
        "info": {},
    }
    mock_exchange.create_order.return_value = _make_raw_order(
        symbol="BTC/KRW", side="buy"
    )
    mock_exchange.cancel_order.return_value = {"id": "123", "status": "canceled"}
    mock_exchange.fetch_ticker.return_value = _make_raw_ticker("BTC/KRW")
    mock_exchange.fetch_ohlcv.return_value = _make_raw_ohlcv()

    # Patch at src.exchange.upbit because upbit.py imports _import_ccxt at module load
    with patch("src.exchange.upbit._import_ccxt") as mock_import:
        mock_ccxt = MagicMock()
        mock_ccxt.upbit.return_value = mock_exchange
        mock_ccxt.AuthenticationError = Exception
        mock_ccxt.RateLimitExceeded = Exception
        mock_ccxt.InsufficientFunds = Exception
        mock_ccxt.OrderNotFound = Exception
        mock_ccxt.NetworkError = Exception
        mock_ccxt.ExchangeNotAvailable = Exception
        mock_import.return_value = mock_ccxt
        yield mock_exchange


@pytest.fixture()
def upbit_client(mock_ccxt_upbit: MagicMock) -> UpbitClient:
    return UpbitClient(access_key="test_key", secret_key="test_secret")


class TestUpbitClient:
    def test_exchange_id(self, upbit_client: UpbitClient) -> None:
        assert upbit_client.exchange_id == "upbit"

    def test_get_balance(self, upbit_client: UpbitClient) -> None:
        balances = upbit_client.get_balance()
        assert "BTC" in balances
        assert "KRW" in balances
        assert balances["KRW"].total == Decimal("1000000.0")

    def test_place_order(self, upbit_client: UpbitClient) -> None:
        order = upbit_client.place_order("BTC/KRW", "buy", 0.01, price=50000000.0)
        assert order.side == "buy"
        assert order.status == OrderStatus.OPEN

    def test_cancel_order_success(self, upbit_client: UpbitClient) -> None:
        result = upbit_client.cancel_order("123", "BTC/KRW")
        assert result is True

    def test_cancel_order_not_found(self) -> None:
        class FakeOrderNotFound(Exception):
            pass

        mock_exchange = MagicMock()
        mock_exchange.cancel_order.side_effect = FakeOrderNotFound("not found")

        mock_ccxt = MagicMock()
        mock_ccxt.OrderNotFound = FakeOrderNotFound
        mock_ccxt.AuthenticationError = type("AuthErr", (Exception,), {})
        mock_ccxt.RateLimitExceeded = type("RateLimit", (Exception,), {})
        mock_ccxt.InsufficientFunds = type("InsFunds", (Exception,), {})
        mock_ccxt.NetworkError = type("NetErr", (Exception,), {})
        mock_ccxt.ExchangeNotAvailable = type("ExchNA", (Exception,), {})
        mock_ccxt.upbit.return_value = mock_exchange

        # Must patch both: upbit._import_ccxt (for __init__) and
        # binance._import_ccxt (used by _map_ccxt_exception)
        with patch("src.exchange.upbit._import_ccxt", return_value=mock_ccxt), \
             patch("src.exchange.binance._import_ccxt", return_value=mock_ccxt):
            client = UpbitClient("k", "s")
            result = client.cancel_order("999", "BTC/KRW")
            assert result is False

    def test_get_ohlcv(self, upbit_client: UpbitClient) -> None:
        bars = upbit_client.get_ohlcv("BTC/KRW", "1h", 1)
        assert len(bars) == 1
        assert bars[0].open == Decimal("50000.0")

    def test_get_ohlcv_dataframe(self, upbit_client: UpbitClient) -> None:
        df = upbit_client.get_ohlcv_dataframe("BTC/KRW", "1h", 1)
        assert "close" in df.columns

    def test_get_ticker(self, upbit_client: UpbitClient) -> None:
        ticker = upbit_client.get_ticker("BTC/KRW")
        assert ticker.symbol == "BTC/KRW"
        assert ticker.last == Decimal("50000.0")
