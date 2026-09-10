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


class TestGetTopSymbolsByVolume:
    """
    SKR/KRW 사고(24h 거래대금 2위였는데 진입 4분 만에 손절가를 뚫고 급락 —
    실제로는 고가/저가 범위가 60%에 달하는 펌프/덤프 종목이었음) 이후 추가된
    변동성 필터 검증.
    """

    def _setup(self, mock_ccxt_upbit: MagicMock, tickers: dict) -> None:
        mock_ccxt_upbit.markets = {sym: {} for sym in tickers}
        mock_ccxt_upbit.fetch_tickers.return_value = tickers

    def test_excludes_symbol_exceeding_volatility_cap(
        self, upbit_client: UpbitClient, mock_ccxt_upbit: MagicMock
    ) -> None:
        # SKR: 거래대금 1위지만 high/low 범위 60% (실제 사고 재현) → 제외돼야 함
        # BTC: 거래대금 2위, 범위 1.5% (정상) → 통과해야 함
        self._setup(
            mock_ccxt_upbit,
            {
                "SKR/KRW": {"quoteVolume": 200_000_000_000, "high": 48.8, "low": 30.5},
                "BTC/KRW": {"quoteVolume": 100_000_000_000, "high": 108862000, "low": 107255000},
            },
        )
        result = upbit_client.get_top_symbols_by_volume(n=10, max_volatility_pct=20.0)
        assert "SKR/KRW" not in result
        assert result == ["BTC/KRW"]

    def test_default_cap_is_20_pct(
        self, upbit_client: UpbitClient, mock_ccxt_upbit: MagicMock
    ) -> None:
        self._setup(
            mock_ccxt_upbit,
            {
                "SKR/KRW": {"quoteVolume": 200_000_000_000, "high": 48.8, "low": 30.5},
                "BTC/KRW": {"quoteVolume": 100_000_000_000, "high": 108862000, "low": 107255000},
            },
        )
        result = upbit_client.get_top_symbols_by_volume(n=10)  # max_volatility_pct 기본값
        assert result == ["BTC/KRW"]

    def test_zero_or_negative_cap_disables_filter(
        self, upbit_client: UpbitClient, mock_ccxt_upbit: MagicMock
    ) -> None:
        self._setup(
            mock_ccxt_upbit,
            {
                "SKR/KRW": {"quoteVolume": 200_000_000_000, "high": 48.8, "low": 30.5},
                "BTC/KRW": {"quoteVolume": 100_000_000_000, "high": 108862000, "low": 107255000},
            },
        )
        result = upbit_client.get_top_symbols_by_volume(n=10, max_volatility_pct=0)
        assert result == ["SKR/KRW", "BTC/KRW"]

    def test_missing_high_low_passes_through(
        self, upbit_client: UpbitClient, mock_ccxt_upbit: MagicMock
    ) -> None:
        """high/low 정보가 없는 종목(신규상장 등)은 보수적으로 통과시킨다."""
        self._setup(
            mock_ccxt_upbit,
            {"NEW/KRW": {"quoteVolume": 50_000_000_000}},
        )
        result = upbit_client.get_top_symbols_by_volume(n=10, max_volatility_pct=20.0)
        assert result == ["NEW/KRW"]

    def test_still_respects_n_after_filtering(
        self, upbit_client: UpbitClient, mock_ccxt_upbit: MagicMock
    ) -> None:
        self._setup(
            mock_ccxt_upbit,
            {
                "SKR/KRW": {"quoteVolume": 300_000_000_000, "high": 48.8, "low": 30.5},  # excluded
                "BTC/KRW": {"quoteVolume": 200_000_000_000, "high": 108862000, "low": 107255000},
                "XRP/KRW": {"quoteVolume": 100_000_000_000, "high": 1905, "low": 1860},
                "ETH/KRW": {"quoteVolume": 50_000_000_000, "high": 5000000, "low": 4950000},
            },
        )
        result = upbit_client.get_top_symbols_by_volume(n=2, max_volatility_pct=20.0)
        assert result == ["BTC/KRW", "XRP/KRW"]

    def test_excludes_symbol_below_min_price(
        self, upbit_client: UpbitClient, mock_ccxt_upbit: MagicMock
    ) -> None:
        """
        대시보드 신호평가 목록이 1,000원 미만 코인 제외 필터 적용 후 만성적으로
        7개 수준밖에 안 채워지던 문제의 원인 재현 — 추적 풀(top-N) 선정 시점
        에서는 가격을 전혀 보지 않아 저가 코인이 top-N 슬롯을 차지했었다.
        min_price_krw 지정 시 순위 산정 전에 저가 코인을 제외해야 한다.
        """
        self._setup(
            mock_ccxt_upbit,
            {
                "PENNY/KRW": {"quoteVolume": 300_000_000_000, "high": 51, "low": 50, "last": 50},
                "BTC/KRW": {"quoteVolume": 100_000_000_000, "high": 108862000, "low": 107255000, "last": 108000000},
            },
        )
        result = upbit_client.get_top_symbols_by_volume(
            n=10, max_volatility_pct=20.0, min_price_krw=1000.0
        )
        assert result == ["BTC/KRW"]

    def test_min_price_default_is_zero_no_filtering(
        self, upbit_client: UpbitClient, mock_ccxt_upbit: MagicMock
    ) -> None:
        """min_price_krw를 지정하지 않으면 기존과 동일하게 가격으로 거르지 않는다."""
        self._setup(
            mock_ccxt_upbit,
            {"PENNY/KRW": {"quoteVolume": 300_000_000_000, "high": 51, "low": 50, "last": 50}},
        )
        result = upbit_client.get_top_symbols_by_volume(n=10, max_volatility_pct=20.0)
        assert result == ["PENNY/KRW"]


class TestGetLiquidSymbols:
    """
    브레이크아웃 스캐너 404 "Code not found" 사고 회귀 테스트.

    근본 원인: self._exchange.markets가 한 번 채워지면 프로세스 수명 내내
    재조회하지 않아, 그 사이 업비트에서 상장폐지/변경된 마켓 코드가 캐시에
    남는다. ccxt upbit.fetch_tickers()는 심볼 목록을 하나의 배치 요청
    (?markets=A,B,C...)으로 묶어 보내므로, 캐시에 남은 단 하나의 무효
    마켓 코드가 배치 전체를 404 "Code not found"로 실패시킨다 — 42시간
    동안 신규 진입 스캔이 전부 막혔던 실제 장애(2026-09-08~09-10, 505회
    연속 실패)의 원인. 컨테이너 재시작으로 마켓 목록이 새로 로드되면서
    우연히 해소됐을 뿐, 코드 차원의 근본 수정은 아니었다.

    따라서 get_liquid_symbols()는 매 호출마다 마켓 목록을 강제로 새로
    불러와야 한다(get_top_symbols_by_volume()과 달리 — 그쪽은 라이브
    엔진이 10분마다 호출하는 별도 경로라 이 수정의 영향을 받지 않는다).
    """

    def _setup(self, mock_ccxt_upbit: MagicMock, tickers: dict) -> None:
        mock_ccxt_upbit.markets = {sym: {} for sym in tickers}
        mock_ccxt_upbit.fetch_tickers.return_value = tickers

    def test_forces_fresh_market_reload_every_call(
        self, upbit_client: UpbitClient, mock_ccxt_upbit: MagicMock
    ) -> None:
        """
        self._exchange.markets가 이미 채워져 있어도(캐시된 상태) 매 호출마다
        load_markets(reload=True)를 호출해 상장폐지된 마켓 코드가 캐시에
        남아있지 않도록 강제해야 한다.
        """
        self._setup(mock_ccxt_upbit, {"BTC/KRW": {"quoteVolume": 100_000_000_000}})

        upbit_client.get_liquid_symbols(min_quote_volume_krw=1_000_000_000)

        mock_ccxt_upbit.load_markets.assert_called_once_with(reload=True)

    def test_returns_symbols_at_or_above_threshold_sorted_desc(
        self, upbit_client: UpbitClient, mock_ccxt_upbit: MagicMock
    ) -> None:
        self._setup(
            mock_ccxt_upbit,
            {
                "BTC/KRW": {"quoteVolume": 200_000_000_000},
                "ETH/KRW": {"quoteVolume": 150_000_000_000},
                "LOW/KRW": {"quoteVolume": 1_000_000_000},
            },
        )
        result = upbit_client.get_liquid_symbols(min_quote_volume_krw=10_000_000_000)
        assert result == ["BTC/KRW", "ETH/KRW"]
