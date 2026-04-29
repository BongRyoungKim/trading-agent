"""
Upbit exchange client wrapping ccxt.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from loguru import logger

from src.exchange.base import BaseExchangeClient
from src.exchange.binance import _map_ccxt_exception


def _import_ccxt():  # type: ignore[no-untyped-def]
    try:
        import ccxt
        return ccxt
    except ImportError as exc:
        raise ImportError("ccxt is required: pip install ccxt") from exc
from src.exchange.models import Balance, OHLCVBar, Order, Ticker
from src.utils.exceptions import OrderNotFoundError
from src.utils.retry import retry


class UpbitClient(BaseExchangeClient):
    """
    Upbit REST API client.

    Args:
        access_key: Upbit access key (empty string for public endpoints).
        secret_key: Upbit secret key.
    """

    def __init__(self, access_key: str = "", secret_key: str = "") -> None:
        ccxt = _import_ccxt()
        self._exchange = ccxt.upbit(
            {
                "apiKey": access_key,
                "secret": secret_key,
                "enableRateLimit": True,
            }
        )
        logger.debug("UpbitClient initialized")

    @property
    def exchange_id(self) -> str:
        return "upbit"

    @retry(max_attempts=3, base_delay=1.0)
    def get_balance(self) -> dict[str, Balance]:
        try:
            raw = self._exchange.fetch_balance()
            # ccxt Upbit puts the raw Upbit API array in raw['info'].
            # avg_buy_price lives there, NOT in per-currency dicts.
            avg_buy_prices: dict[str, Decimal] = {}
            info_list = raw.get("info", [])
            if isinstance(info_list, list):
                for item in info_list:
                    if isinstance(item, dict):
                        cur = item.get("currency", "")
                        abp = item.get("avg_buy_price")
                        if cur and abp:
                            avg_buy_prices[cur] = Decimal(str(abp))

            return {
                currency: Balance(
                    currency=currency,
                    free=Decimal(str(data.get("free") or 0)),
                    used=Decimal(str(data.get("used") or 0)),
                    total=Decimal(str(data.get("total") or 0)),
                    avg_buy_price=avg_buy_prices.get(currency, Decimal(0)),
                )
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
            if order_type == "market" and side == "buy":
                # Upbit market buy requires KRW cost, not base-currency amount.
                # ccxt Upbit reads the 'cost' param and ignores 'amount' for market buys.
                ticker = self._exchange.fetch_ticker(symbol)
                current_price = float(ticker["last"])
                krw_cost = float(amount) * current_price
                raw = self._exchange.create_order(
                    symbol=symbol,
                    type=order_type,
                    side=side,
                    amount=None,
                    price=None,
                    params={"cost": krw_cost},
                )
            else:
                raw = self._exchange.create_order(
                    symbol=symbol,
                    type=order_type,
                    side=side,
                    amount=amount,
                    price=price,
                )
            order_id = raw.get("id")
            logger.info(
                "Order placed",
                exchange=self.exchange_id,
                symbol=symbol,
                side=side,
                order_id=order_id,
            )
            # Upbit market orders are IOC: poll once to get actual fill details
            if raw.get("type") == "market" and order_id:
                import time as _time
                _time.sleep(0.5)
                try:
                    raw = self._exchange.fetch_order(order_id, symbol)
                except Exception:  # noqa: BLE001
                    pass  # fallback to original response
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

    @retry(max_attempts=3, base_delay=2.0)
    def get_top_symbols_by_volume(self, n: int = 20) -> list[str]:
        """
        Return top N active KRW-market symbols ranked by 24h quote volume.
        Fetches only KRW markets to avoid Upbit's URL length limit (HTTP 400)
        that occurs when all BTC/KRW/USDT markets are passed in one request.
        """
        try:
            if not self._exchange.markets:
                self._exchange.load_markets()
            krw_symbols = [s for s in self._exchange.markets if s.endswith("/KRW")]
            tickers = self._exchange.fetch_tickers(krw_symbols)
            ranked = sorted(
                (
                    (sym, float(data.get("quoteVolume") or 0))
                    for sym, data in tickers.items()
                    if sym.endswith("/KRW") and (data.get("quoteVolume") or 0) > 0
                ),
                key=lambda x: x[1],
                reverse=True,
            )
            return [sym for sym, _ in ranked[:n]]
        except Exception as exc:
            raise _map_ccxt_exception(exc, "get_top_symbols_by_volume") from exc
