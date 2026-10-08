"""Unit tests for src/vpin_signal/scanner.py (wiring layer, exchange/telegram/binance mocked)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from src.exchange.models import OHLCVBar, Ticker
from src.vpin_signal.exits import VPINExitConfig
from src.vpin_signal.scanner import VPINScanner, VPINScannerConfig
from src.vpin_signal.signal import MIN_BARS_REQUIRED, WINDOW
from src.portfolio.tracker import PortfolioTracker


def _make_bar(price: float) -> OHLCVBar:
    return OHLCVBar(
        timestamp=datetime.now(UTC), open=Decimal(str(price)), high=Decimal(str(price * 1.01)),
        low=Decimal(str(price * 0.99)), close=Decimal(str(price)), volume=Decimal("1"),
    )


def _make_ticker(symbol: str, price: float) -> Ticker:
    return Ticker(
        symbol=symbol, bid=Decimal(str(price)), ask=Decimal(str(price)),
        last=Decimal(str(price)), volume=Decimal("1"), timestamp=datetime.now(UTC),
    )


def _make_binance_df(n: int, bullish: bool) -> pd.DataFrame:
    """bullish=True면 마지막 WINDOW개 봉에서 강한 매수우위 불균형 생성."""
    rng = np.random.default_rng(0)
    timestamps = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    volumes = np.full(n, 1000.0) + rng.normal(0, 1, n)
    deltas = rng.normal(0, 1, n)
    df = pd.DataFrame({"timestamp": timestamps, "volume": volumes, "delta": deltas})
    if bullish:
        df.loc[df.index[-WINDOW:], "delta"] = 5000.0
        df.loc[df.index[-WINDOW:], "volume"] = 5500.0
    return df


def _make_scanner(**overrides) -> tuple[VPINScanner, MagicMock, MagicMock, PortfolioTracker, MagicMock]:
    exchange = MagicMock()
    telegram = MagicMock()
    portfolio = PortfolioTracker(initial_cash=Decimal("1000000"))
    binance_fetcher = MagicMock()
    exit_cfg = overrides.get("exit_cfg") or VPINExitConfig()
    scanner_cfg = overrides.get("scanner_cfg") or VPINScannerConfig(
        symbol_pairs=(("BTC/KRW", "BTCUSDT"), ("ETH/KRW", "ETHUSDT")),
        position_size_krw=Decimal("100000"), max_concurrent_positions=2,
    )
    scanner = VPINScanner(
        exchange=exchange, telegram=telegram, portfolio=portfolio,
        exit_cfg=exit_cfg, scanner_cfg=scanner_cfg, binance_fetcher=binance_fetcher,
    )
    return scanner, exchange, telegram, portfolio, binance_fetcher


class TestRefreshSignalAndScan:
    def test_opens_position_on_bullish_signal(self) -> None:
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner()
        n = MIN_BARS_REQUIRED + 10
        fetcher.return_value = _make_binance_df(n, bullish=True)
        exchange.get_ohlcv.return_value = [_make_bar(1000.0) for _ in range(20)]
        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 1000.0)

        scanner.refresh_signal_and_scan()

        assert len(scanner.open_symbols) > 0
        telegram.send.assert_called()

    def test_no_entry_without_bullish_signal(self) -> None:
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner()
        n = MIN_BARS_REQUIRED + 10
        fetcher.return_value = _make_binance_df(n, bullish=False)

        scanner.refresh_signal_and_scan()

        assert scanner.open_symbols == []
        exchange.get_ticker.assert_not_called()

    def test_no_signal_when_insufficient_binance_history(self) -> None:
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner()
        fetcher.return_value = _make_binance_df(MIN_BARS_REQUIRED - 10, bullish=True)

        scanner.refresh_signal_and_scan()

        assert scanner.open_symbols == []

    def test_respects_max_concurrent_positions(self) -> None:
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner(
            scanner_cfg=VPINScannerConfig(
                symbol_pairs=(("BTC/KRW", "BTCUSDT"), ("ETH/KRW", "ETHUSDT"), ("XRP/KRW", "XRPUSDT")),
                position_size_krw=Decimal("100000"), max_concurrent_positions=1,
            )
        )
        n = MIN_BARS_REQUIRED + 10
        fetcher.return_value = _make_binance_df(n, bullish=True)
        exchange.get_ohlcv.return_value = [_make_bar(1000.0) for _ in range(20)]
        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 1000.0)

        scanner.refresh_signal_and_scan()

        assert len(scanner.open_symbols) == 1

    def test_binance_fetch_failure_is_skipped_not_raised(self) -> None:
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner()
        fetcher.side_effect = RuntimeError("binance down")

        scanner.refresh_signal_and_scan()  # 예외 전파하지 않아야 함

        assert scanner.open_symbols == []

    def test_atr_unavailable_skips_entry(self) -> None:
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner()
        n = MIN_BARS_REQUIRED + 10
        fetcher.return_value = _make_binance_df(n, bullish=True)
        exchange.get_ohlcv.return_value = []  # ATR 계산 불가

        scanner.refresh_signal_and_scan()

        assert scanner.open_symbols == []


class TestCheckExits:
    def test_no_op_when_no_positions(self) -> None:
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner()
        scanner.check_exits()
        exchange.get_ticker.assert_not_called()

    def test_closes_position_on_stop_loss(self) -> None:
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner()
        n = MIN_BARS_REQUIRED + 10
        fetcher.return_value = _make_binance_df(n, bullish=True)
        exchange.get_ohlcv.return_value = [_make_bar(1000.0) for _ in range(20)]
        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 1000.0)
        scanner.refresh_signal_and_scan()
        assert len(scanner.open_symbols) > 0
        opened_symbol = scanner.open_symbols[0]

        exchange.get_ticker.return_value = _make_ticker(opened_symbol, 500.0)  # 큰 폭 하락
        scanner.check_exits()

        assert opened_symbol not in scanner.open_symbols
        assert not portfolio.has_position(opened_symbol)
        telegram.send_position_closed.assert_called()

    def test_closes_position_after_max_hold(self) -> None:
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner(
            exit_cfg=VPINExitConfig(max_hold_hours=20.0),
        )
        n = MIN_BARS_REQUIRED + 10
        fetcher.return_value = _make_binance_df(n, bullish=True)
        exchange.get_ohlcv.return_value = [_make_bar(1000.0) for _ in range(20)]
        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 1000.0)
        scanner.refresh_signal_and_scan()
        opened_symbol = scanner.open_symbols[0]

        scanner._positions[opened_symbol].entry_time = datetime.now(UTC) - timedelta(hours=21)
        scanner.check_exits()

        assert opened_symbol not in scanner.open_symbols
        telegram.send_position_closed.assert_called()
