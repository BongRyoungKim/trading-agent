"""Unit tests for src/exchange/models.py"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from src.exchange.models import (
    Balance,
    OHLCVBar,
    Order,
    OrderStatus,
    OrderType,
    Ticker,
)


class TestBalance:
    def test_from_ccxt_basic(self) -> None:
        data = {"free": 1.5, "used": 0.5, "total": 2.0}
        b = Balance.from_ccxt("BTC", data)
        assert b.currency == "BTC"
        assert b.free == Decimal("1.5")
        assert b.used == Decimal("0.5")
        assert b.total == Decimal("2.0")

    def test_from_ccxt_none_values(self) -> None:
        data = {"free": None, "used": None, "total": None}
        b = Balance.from_ccxt("USDT", data)
        assert b.free == Decimal("0")
        assert b.total == Decimal("0")

    def test_balance_is_immutable(self) -> None:
        b = Balance("BTC", Decimal("1"), Decimal("0"), Decimal("1"))
        with pytest.raises((AttributeError, TypeError)):
            b.free = Decimal("99")  # type: ignore[misc]


class TestOrder:
    def _raw_order(self) -> dict:
        return {
            "id": "ord_123",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "limit",
            "amount": 0.01,
            "price": 50000.0,
            "filled": 0.0,
            "status": "open",
            "timestamp": 1700000000000,
        }

    def test_from_ccxt_basic(self) -> None:
        o = Order.from_ccxt(self._raw_order())
        assert o.id == "ord_123"
        assert o.symbol == "BTC/USDT"
        assert o.side == "buy"
        assert o.order_type == OrderType.LIMIT
        assert o.amount == Decimal("0.01")
        assert o.price == Decimal("50000.0")
        assert o.status == OrderStatus.OPEN

    def test_from_ccxt_timestamp_conversion(self) -> None:
        o = Order.from_ccxt(self._raw_order())
        assert isinstance(o.created_at, datetime)

    def test_from_ccxt_no_timestamp(self) -> None:
        raw = {**self._raw_order(), "timestamp": None}
        o = Order.from_ccxt(raw)
        assert isinstance(o.created_at, datetime)

    def test_order_is_immutable(self) -> None:
        o = Order.from_ccxt(self._raw_order())
        with pytest.raises((AttributeError, TypeError)):
            o.side = "sell"  # type: ignore[misc]


class TestTicker:
    def _raw_ticker(self) -> dict:
        return {
            "symbol": "BTC/USDT",
            "bid": 49900.0,
            "ask": 50100.0,
            "last": 50000.0,
            "baseVolume": 1234.56,
            "timestamp": 1700000000000,
        }

    def test_from_ccxt_basic(self) -> None:
        t = Ticker.from_ccxt(self._raw_ticker())
        assert t.symbol == "BTC/USDT"
        assert t.bid == Decimal("49900.0")
        assert t.ask == Decimal("50100.0")
        assert t.last == Decimal("50000.0")
        assert t.volume == Decimal("1234.56")

    def test_ticker_is_immutable(self) -> None:
        t = Ticker.from_ccxt(self._raw_ticker())
        with pytest.raises((AttributeError, TypeError)):
            t.last = Decimal("0")  # type: ignore[misc]


class TestOHLCVBar:
    def _raw_row(self) -> list:
        return [1700000000000, 50000.0, 51000.0, 49000.0, 50500.0, 100.0]

    def test_from_ccxt_basic(self) -> None:
        bar = OHLCVBar.from_ccxt(self._raw_row())
        assert bar.open == Decimal("50000.0")
        assert bar.high == Decimal("51000.0")
        assert bar.low == Decimal("49000.0")
        assert bar.close == Decimal("50500.0")
        assert bar.volume == Decimal("100.0")
        assert isinstance(bar.timestamp, datetime)

    def test_ohlcvbar_is_immutable(self) -> None:
        bar = OHLCVBar.from_ccxt(self._raw_row())
        with pytest.raises((AttributeError, TypeError)):
            bar.close = Decimal("0")  # type: ignore[misc]
