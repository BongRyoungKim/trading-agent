"""Unit tests for src/ml_signal/scanner.py (wiring layer, exchange/telegram/model mocked)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.exchange.models import OHLCVBar, Ticker
from src.ml_signal.exits import MLSignalExitConfig
from src.ml_signal.scanner import MLSignalScanner, MLSignalScannerConfig
from src.portfolio.tracker import PortfolioTracker


def _make_bars(n: int, price: float = 1000.0) -> list[OHLCVBar]:
    """MIN_BARS_REQUIRED를 넘는 합성 OHLCV — 실제 노이즈 있는 시계열이라
    features.compute_features()가 NaN 없이 계산 가능하다."""
    rng = np.random.default_rng(1)
    closes = price + np.cumsum(rng.normal(0, price * 0.01, n))
    closes = np.clip(closes, 1, None)
    now = datetime.now(UTC)
    return [
        OHLCVBar(
            timestamp=now - timedelta(days=n - i),
            open=Decimal(str(closes[i])), high=Decimal(str(closes[i] * 1.01)),
            low=Decimal(str(closes[i] * 0.99)), close=Decimal(str(closes[i])),
            volume=Decimal("1000"),
        )
        for i in range(n)
    ]


def _make_ticker(symbol: str, price: float) -> Ticker:
    return Ticker(
        symbol=symbol, bid=Decimal(str(price)), ask=Decimal(str(price)),
        last=Decimal(str(price)), volume=Decimal("1"), timestamp=datetime.now(UTC),
    )


def _make_scanner(**overrides) -> tuple[MLSignalScanner, MagicMock, MagicMock, PortfolioTracker, MagicMock]:
    exchange = MagicMock()
    telegram = MagicMock()
    portfolio = PortfolioTracker(initial_cash=Decimal("1000000"))
    model = MagicMock()
    model.predict_proba.return_value = 0.0
    exit_cfg = overrides.get("exit_cfg") or MLSignalExitConfig()
    scanner_cfg = overrides.get("scanner_cfg") or MLSignalScannerConfig(
        symbols=("BTC/KRW", "ETH/KRW"), position_size_krw=Decimal("100000"),
        max_concurrent_positions=2, entry_threshold=0.5,
    )
    scanner = MLSignalScanner(
        exchange=exchange, telegram=telegram, portfolio=portfolio, model=model,
        exit_cfg=exit_cfg, scanner_cfg=scanner_cfg,
    )
    return scanner, exchange, telegram, portfolio, model


class TestRefreshSignalAndScan:
    def test_opens_position_when_probability_above_threshold(self) -> None:
        scanner, exchange, telegram, portfolio, model = _make_scanner()
        exchange.get_ohlcv.return_value = _make_bars(90)
        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 1000.0)
        model.predict_proba.return_value = 0.8

        scanner.refresh_signal_and_scan()

        assert "BTC/KRW" in scanner.open_symbols
        assert portfolio.has_position("BTC/KRW")
        telegram.send.assert_called()

    def test_no_entry_when_probability_at_or_below_threshold(self) -> None:
        scanner, exchange, telegram, portfolio, model = _make_scanner()
        exchange.get_ohlcv.return_value = _make_bars(90)
        model.predict_proba.return_value = 0.5

        scanner.refresh_signal_and_scan()

        assert scanner.open_symbols == []
        exchange.get_ticker.assert_not_called()

    def test_no_signal_when_insufficient_history(self) -> None:
        scanner, exchange, telegram, portfolio, model = _make_scanner()
        exchange.get_ohlcv.return_value = _make_bars(10)  # < MIN_BARS_REQUIRED
        model.predict_proba.return_value = 0.9

        scanner.refresh_signal_and_scan()

        assert scanner.open_symbols == []
        model.predict_proba.assert_not_called()

    def test_skips_symbol_already_held(self) -> None:
        scanner, exchange, telegram, portfolio, model = _make_scanner()
        exchange.get_ohlcv.return_value = _make_bars(90)
        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 1000.0)
        model.predict_proba.return_value = 0.8
        scanner.refresh_signal_and_scan()
        assert "BTC/KRW" in scanner.open_symbols
        model.predict_proba.reset_mock()

        scanner.refresh_signal_and_scan()

        # BTC already held -> only ETH should be scanned this time
        assert model.predict_proba.call_count <= 1

    def test_stops_scanning_once_max_concurrent_positions_reached(self) -> None:
        scanner, exchange, telegram, portfolio, model = _make_scanner(
            scanner_cfg=MLSignalScannerConfig(
                symbols=("BTC/KRW", "ETH/KRW", "XRP/KRW"),
                position_size_krw=Decimal("100000"),
                max_concurrent_positions=1, entry_threshold=0.5,
            )
        )
        exchange.get_ohlcv.return_value = _make_bars(90)
        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 1000.0)
        model.predict_proba.return_value = 0.9

        scanner.refresh_signal_and_scan()

        assert len(scanner.open_symbols) == 1  # slot limit respected

    def test_ohlcv_fetch_failure_is_skipped_not_raised(self) -> None:
        scanner, exchange, telegram, portfolio, model = _make_scanner()
        exchange.get_ohlcv.side_effect = RuntimeError("api down")

        scanner.refresh_signal_and_scan()  # 예외 전파하지 않아야 함

        assert scanner.open_symbols == []


class TestCheckExits:
    def test_no_op_when_no_positions(self) -> None:
        scanner, exchange, telegram, portfolio, model = _make_scanner()
        scanner.check_exits()
        exchange.get_ticker.assert_not_called()

    def test_closes_position_on_safety_stop_loss(self) -> None:
        scanner, exchange, telegram, portfolio, model = _make_scanner()
        exchange.get_ohlcv.return_value = _make_bars(90)
        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 1000.0)
        model.predict_proba.return_value = 0.8
        scanner.refresh_signal_and_scan()
        assert "BTC/KRW" in scanner.open_symbols

        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 700.0)  # -30%, well below 20% safety stop
        scanner.check_exits()

        assert "BTC/KRW" not in scanner.open_symbols
        assert not portfolio.has_position("BTC/KRW")
        telegram.send_position_closed.assert_called()

    def test_closes_position_after_hold_days_elapsed(self) -> None:
        scanner, exchange, telegram, portfolio, model = _make_scanner(
            exit_cfg=MLSignalExitConfig(hold_days=5, safety_stop_pct=20.0),
        )
        exchange.get_ohlcv.return_value = _make_bars(90)
        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 1000.0)
        model.predict_proba.return_value = 0.8
        scanner.refresh_signal_and_scan()
        assert "BTC/KRW" in scanner.open_symbols

        position = scanner._positions["BTC/KRW"]  # noqa: SLF001 -- test-only introspection
        position.entry_time = datetime.now(UTC) - timedelta(days=6)

        scanner.check_exits()

        assert "BTC/KRW" not in scanner.open_symbols
        telegram.send_position_closed.assert_called()
