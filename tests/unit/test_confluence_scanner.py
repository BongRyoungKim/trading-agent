"""Unit tests for src/confluence/scanner.py (wiring layer, exchange/telegram mocked)."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from src.confluence.exits import ConfluenceExitConfig
from src.confluence.scanner import ConfluenceScanner, ConfluenceScannerConfig
from src.exchange.models import OHLCVBar, Ticker
from src.portfolio.tracker import PortfolioTracker


def _bar(price: float, volume: float = 1.0) -> OHLCVBar:
    return OHLCVBar(
        timestamp=datetime.now(UTC),
        open=Decimal(str(price)), high=Decimal(str(price)),
        low=Decimal(str(price)), close=Decimal(str(price)),
        volume=Decimal(str(volume)),
    )


def _uptrend_bars(n: int, start: float = 100_000_000, step: float = 100_000) -> list[OHLCVBar]:
    return [_bar(start + i * step) for i in range(n)]


def _flat_bars(n: int, price: float = 100_000_000) -> list[OHLCVBar]:
    return [_bar(price) for _ in range(n)]


def _ticker(price: float) -> Ticker:
    return Ticker(
        symbol="BTC/KRW", bid=Decimal(str(price)), ask=Decimal(str(price)),
        last=Decimal(str(price)), volume=Decimal("1"), timestamp=datetime.now(UTC),
    )


def _make_scanner(**overrides) -> tuple[ConfluenceScanner, MagicMock, MagicMock, PortfolioTracker]:
    exchange = MagicMock()
    telegram = MagicMock()
    portfolio = PortfolioTracker(initial_cash=Decimal("1000000"))
    exit_cfg = overrides.get("exit_cfg") or ConfluenceExitConfig()
    scanner_cfg = overrides.get("scanner_cfg") or ConfluenceScannerConfig()
    scanner = ConfluenceScanner(
        exchange=exchange, telegram=telegram, portfolio=portfolio,
        exit_cfg=exit_cfg, scanner_cfg=scanner_cfg,
    )
    return scanner, exchange, telegram, portfolio


class TestRefreshSignalAndScan:
    def test_opens_position_when_confluence_and_breakout(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()

        def get_ohlcv(symbol, timeframe, limit):
            if timeframe in ("1d", "4h"):
                return _uptrend_bars(60)
            # 1h: 마지막 봉이 직전 breakout_window 최고종가를 강하게 돌파+거래량 급증
            bars = _flat_bars(30, price=100_000_000)
            bars.append(_bar(105_000_000, volume=100.0))
            return bars

        exchange.get_ohlcv.side_effect = get_ohlcv
        exchange.get_ticker.return_value = _ticker(105_000_000)

        scanner.refresh_signal_and_scan()

        assert scanner.has_position
        assert portfolio.has_position("BTC/KRW")
        telegram.send.assert_called()

    def test_no_entry_when_htf_not_uptrend(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()
        exchange.get_ohlcv.side_effect = lambda symbol, timeframe, limit: _flat_bars(60)

        scanner.refresh_signal_and_scan()

        assert not scanner.has_position
        assert exchange.get_ticker.call_count == 0

    def test_no_new_entry_when_already_holding(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()

        def get_ohlcv(symbol, timeframe, limit):
            if timeframe in ("1d", "4h"):
                return _uptrend_bars(60)
            bars = _flat_bars(30, price=100_000_000)
            bars.append(_bar(105_000_000, volume=100.0))
            return bars

        exchange.get_ohlcv.side_effect = get_ohlcv
        exchange.get_ticker.return_value = _ticker(105_000_000)
        scanner.refresh_signal_and_scan()
        assert scanner.has_position
        exchange.get_ticker.reset_mock()

        scanner.refresh_signal_and_scan()
        exchange.get_ticker.assert_not_called()

    def test_keeps_previous_state_when_htf_fetch_fails(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()
        exchange.get_ohlcv.side_effect = RuntimeError("api down")
        scanner.refresh_signal_and_scan()  # 예외 전파 안 해야 함
        assert not scanner.has_position


class TestCheckExits:
    def test_no_op_when_no_position(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()
        scanner.check_exits()
        exchange.get_ticker.assert_not_called()

    def test_closes_on_stop_loss(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()

        def get_ohlcv(symbol, timeframe, limit):
            if timeframe in ("1d", "4h"):
                return _uptrend_bars(60)
            bars = _flat_bars(30, price=100_000_000)
            bars.append(_bar(105_000_000, volume=100.0))
            return bars

        exchange.get_ohlcv.side_effect = get_ohlcv
        exchange.get_ticker.return_value = _ticker(105_000_000)
        scanner.refresh_signal_and_scan()
        assert scanner.has_position

        exchange.get_ticker.return_value = _ticker(50_000_000)  # 크게 하락 -> 손절 확실히 이탈
        scanner.check_exits()

        assert not scanner.has_position
        assert not portfolio.has_position("BTC/KRW")
        telegram.send_position_closed.assert_called()

    def test_closes_on_htf_trend_break(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()

        def get_ohlcv(symbol, timeframe, limit):
            if timeframe in ("1d", "4h"):
                return _uptrend_bars(60)
            bars = _flat_bars(30, price=100_000_000)
            bars.append(_bar(105_000_000, volume=100.0))
            return bars

        exchange.get_ohlcv.side_effect = get_ohlcv
        exchange.get_ticker.return_value = _ticker(105_000_000)
        scanner.refresh_signal_and_scan()
        assert scanner.has_position

        scanner._latest_confluence_ok = False  # 국면 붕괴 시뮬레이션
        exchange.get_ticker.return_value = _ticker(105_500_000)  # 손절 안 걸리는 가격
        scanner.check_exits()

        assert not scanner.has_position
        telegram.send_position_closed.assert_called()
