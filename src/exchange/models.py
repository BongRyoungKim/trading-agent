"""
Immutable domain models for exchange data.
All dataclasses are frozen - never mutate, always create new instances.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal


class OrderStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    STOP_LOSS_LIMIT = "stop_loss_limit"


@dataclass(frozen=True)
class Balance:
    currency: str
    free: Decimal
    used: Decimal
    total: Decimal
    avg_buy_price: Decimal = Decimal(0)

    @classmethod
    def from_ccxt(cls, currency: str, data: dict) -> "Balance":
        info = data.get("info") or {}
        avg_buy_price_raw = info.get("avg_buy_price") or 0
        return cls(
            currency=currency,
            free=Decimal(str(data.get("free") or 0)),
            used=Decimal(str(data.get("used") or 0)),
            total=Decimal(str(data.get("total") or 0)),
            avg_buy_price=Decimal(str(avg_buy_price_raw)),
        )


@dataclass(frozen=True)
class Order:
    id: str
    symbol: str
    side: Literal["buy", "sell"]
    order_type: OrderType
    amount: Decimal
    price: Decimal
    filled: Decimal
    status: OrderStatus
    created_at: datetime
    fee: Decimal | None = None

    @classmethod
    def from_ccxt(cls, data: dict) -> "Order":
        fee_raw = data.get("fee")
        fee = Decimal(str(fee_raw["cost"])) if isinstance(fee_raw, dict) and fee_raw.get("cost") else None
        return cls(
            id=str(data["id"]),
            symbol=data["symbol"],
            side=data["side"],
            order_type=OrderType(data.get("type", "limit")),
            amount=Decimal(str(data.get("amount") or data.get("filled") or 0)),
            price=Decimal(str(data.get("price") or data.get("average") or 0)),
            filled=Decimal(str(data.get("filled") or 0)),
            status=OrderStatus(data.get("status", "open")),
            created_at=datetime.fromtimestamp(data["timestamp"] / 1000)
            if data.get("timestamp")
            else datetime.now(UTC),
            fee=fee,
        )


@dataclass(frozen=True)
class Ticker:
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: Decimal
    timestamp: datetime

    @classmethod
    def from_ccxt(cls, data: dict) -> "Ticker":
        return cls(
            symbol=data["symbol"],
            bid=Decimal(str(data.get("bid") or 0)),
            ask=Decimal(str(data.get("ask") or 0)),
            last=Decimal(str(data.get("last") or 0)),
            volume=Decimal(str(data.get("baseVolume") or 0)),
            timestamp=datetime.fromtimestamp(data["timestamp"] / 1000)
            if data.get("timestamp")
            else datetime.now(UTC),
        )


@dataclass(frozen=True)
class OHLCVBar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @classmethod
    def from_ccxt(cls, row: list) -> "OHLCVBar":
        """row = [timestamp_ms, open, high, low, close, volume]"""
        return cls(
            timestamp=datetime.fromtimestamp(row[0] / 1000),
            open=Decimal(str(row[1])),
            high=Decimal(str(row[2])),
            low=Decimal(str(row[3])),
            close=Decimal(str(row[4])),
            volume=Decimal(str(row[5])),
        )
