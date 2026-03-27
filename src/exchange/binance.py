"""
Binance exchange client wrapping ccxt.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from loguru import logger

from src.exchange.base import BaseExchangeClient
from src.exchange.models import Balance, OHLCVBar, Order, Ticker
from src.utils.exceptions import (
    AuthenticationError,
    ConnectionError,
    InsufficientFundsError,
    OrderNotFoundError,
    RateLimitError,
)
from src.utils.retry import retry

if TYPE_CHECKING:
    import ccxt


def _import_ccxt() -> "ccxt":
    try:
        import ccxt
        return ccxt
    except ImportError as exc:
        raise ImportError(
            "ccxt is required: pip install ccxt"
        ) from exc


def _map_ccxt_exception(exc: Exception, context: str = "") -> Exception:
    """Map ccxt exceptions to domain exceptions."""
    ccxt = _import_ccxt()
    msg = str(exc)
    details = {"context": context, "original": type(exc).__name__}

    if isinstance(exc, ccxt.AuthenticationError):
        return AuthenticationError(f"Authentication failed: {msg}", details=details)
    if isinstance(exc, ccxt.RateLimitExceeded):
        return RateLimitError(f"Rate limit exceeded: {msg}", details=details)
    if isinstance(exc, ccxt.InsufficientFunds):
        return InsufficientFundsError(f"Insufficient funds: {msg}", details=details)
    if isinstance(exc, ccxt.OrderNotFound):
        return OrderNotFoundError(f"Order not found: {msg}", details=details)
    if isinstance(exc, (ccxt.NetworkError, ccxt.ExchangeNotAvailable)):
        return ConnectionError(f"Exchange connection error: {msg}", details=details)
    return exc


class BinanceClient(BaseExchangeClient):
    """
    Binance REST API client.

    Args:
        api_key: Binance API key (empty string for public endpoints).
        secret_key: Binance secret key.
        testnet: Connect to Binance testnet instead of production.
    """

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        testnet: bool = False,
    ) -> None:
        ccxt = _import_ccxt()
        self._exchange: ccxt.binance = ccxt.binance(
            {
                "apiKey": api_key,
                "secret": secret_key,
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
        )
        if testnet:
            self._exchange.set_sandbox_mode(True)
        logger.debug("BinanceClient initialized", testnet=testnet)

    @property
    def exchange_id(self) -> str:
        return "binance"

    @retry(max_attempts=3, base_delay=1.0)
    def get_balance(self) -> dict[str, Balance]:
        try:
            raw = self._exchange.fetch_balance()
            return {
                currency: Balance.from_ccxt(currency, data)
                for currency, data in raw.items()
                if isinstance(data, dict)
                and float(data.get("total") or 0) > 0
            }
        except Exception as exc:
            raise _map_ccxt_exception(exc, "get_balance") from exc

    @retry(max_attempts=3, base_delay=1.0)
    def place_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        amount: float,
        price: float | None = None,
    ) -> Order:
        try:
            order_type = "market" if price is None else "limit"
            raw = self._exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side,
                amount=amount,
                price=price,
            )
            logger.info(
                "Order placed",
                exchange=self.exchange_id,
                symbol=symbol,
                side=side,
                type=order_type,
                amount=amount,
                price=price,
                order_id=raw.get("id"),
            )
            return Order.from_ccxt(raw)
        except Exception as exc:
            raise _map_ccxt_exception(exc, f"place_order:{symbol}") from exc

    @retry(max_attempts=3, base_delay=1.0)
    def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            self._exchange.cancel_order(order_id, symbol)
            logger.info("Order canceled", order_id=order_id, symbol=symbol)
            return True
        except Exception as exc:
            mapped = _map_ccxt_exception(exc, f"cancel_order:{order_id}")
            if isinstance(mapped, OrderNotFoundError):
                return False
            raise mapped from exc

    @retry(max_attempts=3, base_delay=1.0)
    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
    ) -> list[OHLCVBar]:
        try:
            raw = self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            return [OHLCVBar.from_ccxt(row) for row in raw]
        except Exception as exc:
            raise _map_ccxt_exception(exc, f"get_ohlcv:{symbol}:{timeframe}") from exc

    @retry(max_attempts=3, base_delay=1.0)
    def get_ticker(self, symbol: str) -> Ticker:
        try:
            raw = self._exchange.fetch_ticker(symbol)
            return Ticker.from_ccxt(raw)
        except Exception as exc:
            raise _map_ccxt_exception(exc, f"get_ticker:{symbol}") from exc
