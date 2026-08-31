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
    def get_top_symbols_by_volume(
        self, n: int = 20, max_volatility_pct: float = 20.0
    ) -> list[str]:
        """
        Return top N active KRW-market symbols ranked by 24h quote volume.
        Fetches only KRW markets to avoid Upbit's URL length limit (HTTP 400)
        that occurs when all BTC/KRW/USDT markets are passed in one request.

        Symbols whose 24h (high-low)/low range exceeds *max_volatility_pct*
        are excluded regardless of rank. 거래대금 상위권이라고 해서 다 안전한
        건 아니다 — 순간 급등락(펌프/덤프) 중인 종목은 거래량이 커도 가격이
        튀어 손절 주문이 설정가보다 훨씬 나쁜 가격에 체결(슬리피지)되기
        쉽다(실제로 SKR/KRW가 24h 거래대금 2위였는데도 진입 4분 만에 손절가를
        뚫고 급락해 큰 손실이 난 사고가 있었음 — 그 시점 SKR의 24h 고가/저가
        범위는 약 60%였다). 24h 순변동률(%change)만으로는 이런 왕복성 급등락을
        못 잡아내서(오르고 내리면 순변동은 작게 나옴) high/low 범위를 쓴다.
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
                    if sym.endswith("/KRW")
                    and (data.get("quoteVolume") or 0) > 0
                    and _within_volatility_limit(data, max_volatility_pct)
                ),
                key=lambda x: x[1],
                reverse=True,
            )
            return [sym for sym, _ in ranked[:n]]
        except Exception as exc:
            raise _map_ccxt_exception(exc, "get_top_symbols_by_volume") from exc

    @retry(max_attempts=3, base_delay=2.0)
    def get_liquid_symbols(self, min_quote_volume_krw: float) -> list[str]:
        """
        24h 거래대금(원)이 min_quote_volume_krw 이상인 모든 KRW 마켓 심볼을
        거래대금 내림차순으로 반환한다.

        get_top_symbols_by_volume()과 달리 변동성 필터를 적용하지 않는다 —
        이 메서드는 브레이크아웃 조기진입 모듈(src/breakout/)의 1차(넓은)
        후보 필터용이고, 그 모듈은 오히려 변동성이 큰 종목을 찾아내는 게
        목적이라 여기서 걸러내면 안 된다. 라이브 엔진의 심볼 자동선정과는
        완전히 별개 용도.
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
                    if sym.endswith("/KRW")
                    and float(data.get("quoteVolume") or 0) >= min_quote_volume_krw
                ),
                key=lambda x: x[1],
                reverse=True,
            )
            return [sym for sym, _ in ranked]
        except Exception as exc:
            raise _map_ccxt_exception(exc, "get_liquid_symbols") from exc


def _within_volatility_limit(ticker: dict, max_pct: float) -> bool:
    """ccxt 티커의 24h high/low 범위가 max_pct(%) 이하인지 확인.

    high/low 정보가 없으면(일부 신규 상장 종목 등) 보수적으로 통과시킨다 —
    데이터 부족을 이유로 걸러내면 오히려 거래대금 상위 종목이 대거 누락될
    수 있어서다. max_pct <= 0이면 필터를 사실상 끈다(항상 통과).
    """
    if max_pct <= 0:
        return True
    high = ticker.get("high")
    low = ticker.get("low")
    if not high or not low or low <= 0:
        return True
    range_pct = (float(high) - float(low)) / float(low) * 100
    return range_pct <= max_pct
