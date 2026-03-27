"""
Abstract base class for all exchange clients.
Phase 2 implementations: BinanceClient, UpbitClient.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

import pandas as pd

from src.exchange.models import Balance, OHLCVBar, Order, Ticker


class BaseExchangeClient(ABC):
    """
    Exchange-agnostic interface. All exchange clients must implement this.

    Usage:
        client = BinanceClient(api_key=..., secret_key=...)
        ticker = client.get_ticker("BTC/USDT")
    """

    @property
    @abstractmethod
    def exchange_id(self) -> str:
        """Short identifier, e.g. 'binance', 'upbit'."""

    @abstractmethod
    def get_balance(self) -> dict[str, Balance]:
        """
        Return balances for all non-zero currencies.
        Keys are currency codes, e.g. {'BTC': Balance(...), 'USDT': Balance(...)}.
        """

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        amount: float,
        price: float | None = None,
    ) -> Order:
        """
        Place an order. Pass price=None for market orders.

        Args:
            symbol: Market symbol, e.g. 'BTC/USDT'.
            side: 'buy' or 'sell'.
            amount: Quantity in base currency.
            price: Limit price. None = market order.

        Returns:
            The created Order.
        """

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """
        Cancel an open order.

        Returns:
            True if cancellation succeeded, False if already filled/not found.
        """

    @abstractmethod
    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
    ) -> list[OHLCVBar]:
        """
        Fetch OHLCV candles.

        Args:
            symbol: Market symbol, e.g. 'BTC/USDT'.
            timeframe: Candle interval: '1m', '5m', '1h', '1d', etc.
            limit: Number of candles to return.

        Returns:
            List of OHLCVBar, oldest first.
        """

    @abstractmethod
    def get_ticker(self, symbol: str) -> Ticker:
        """Fetch current best bid/ask and last price for symbol."""

    def get_ohlcv_dataframe(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
    ) -> pd.DataFrame:
        """
        Convenience wrapper: returns OHLCV as a pandas DataFrame.
        Columns: timestamp, open, high, low, close, volume (float64).
        Index: RangeIndex.
        """
        bars = self.get_ohlcv(symbol, timeframe, limit)
        rows = [
            {
                "timestamp": b.timestamp,
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
            }
            for b in bars
        ]
        return pd.DataFrame(rows)
