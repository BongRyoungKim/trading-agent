"""Unit tests for src/onchain/scanner.py (wiring layer, exchange/telegram/network mocked)."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.exchange.models import OHLCVBar, Ticker
from src.onchain.exits import OnchainExitConfig
from src.onchain.scanner import OnchainScanner, OnchainScannerConfig
from src.portfolio.tracker import PortfolioTracker


def _make_bar(price: float) -> OHLCVBar:
    return OHLCVBar(
        timestamp=datetime.now(UTC),
        open=Decimal(str(price)), high=Decimal(str(price)),
        low=Decimal(str(price)), close=Decimal(str(price)),
        volume=Decimal("1"),
    )


def _make_ticker(price: float) -> Ticker:
    return Ticker(
        symbol="BTC/KRW", bid=Decimal(str(price)), ask=Decimal(str(price)),
        last=Decimal(str(price)), volume=Decimal("1"), timestamp=datetime.now(UTC),
    )


def _netflow_df(zscore_target: str) -> pd.DataFrame:
    """z-score가 진입/중립/청산 셋 중 하나가 되도록 30일치 합성 데이터 생성."""
    if zscore_target == "strong_outflow":
        netflows = [100.0] * 29 + [-500.0]
    elif zscore_target == "reverted":
        netflows = [-100.0] * 29 + [800.0]
    else:  # neutral
        netflows = [10.0, -5.0, 8.0, -3.0, 6.0] * 6
    dates = pd.date_range("2026-01-01", periods=30, freq="D").date
    return pd.DataFrame({"date": dates, "netflow": netflows})


def _make_scanner(**overrides) -> tuple[OnchainScanner, MagicMock, MagicMock, PortfolioTracker]:
    exchange = MagicMock()
    telegram = MagicMock()
    portfolio = PortfolioTracker(initial_cash=Decimal("1000000"))
    exit_cfg = overrides.get("exit_cfg") or OnchainExitConfig()
    scanner_cfg = overrides.get("scanner_cfg") or OnchainScannerConfig(
        position_size_krw=Decimal("100000"), entry_z_threshold=-1.5,
    )
    scanner = OnchainScanner(
        exchange=exchange, telegram=telegram, portfolio=portfolio,
        exit_cfg=exit_cfg, scanner_cfg=scanner_cfg,
    )
    return scanner, exchange, telegram, portfolio


class TestRefreshSignalAndScan:
    def test_opens_position_when_zscore_below_entry_threshold(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()
        exchange.get_ohlcv.return_value = [_make_bar(100_000_000 + i * 1000) for i in range(20)]
        exchange.get_ticker.return_value = _make_ticker(100_000_000)

        with patch("src.onchain.scanner.fetch_netflow_history", return_value=_netflow_df("strong_outflow")):
            scanner.refresh_signal_and_scan()

        assert scanner.has_position
        assert portfolio.has_position("BTC/KRW")
        telegram.send.assert_called()

    def test_no_entry_when_zscore_not_below_threshold(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()

        with patch("src.onchain.scanner.fetch_netflow_history", return_value=_netflow_df("neutral")):
            scanner.refresh_signal_and_scan()

        assert not scanner.has_position
        exchange.get_ticker.assert_not_called()

    def test_no_new_entry_when_already_holding_position(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()
        exchange.get_ohlcv.return_value = [_make_bar(100_000_000 + i * 1000) for i in range(20)]
        exchange.get_ticker.return_value = _make_ticker(100_000_000)
        with patch("src.onchain.scanner.fetch_netflow_history", return_value=_netflow_df("strong_outflow")):
            scanner.refresh_signal_and_scan()
        assert scanner.has_position
        exchange.get_ticker.reset_mock()

        with patch("src.onchain.scanner.fetch_netflow_history", return_value=_netflow_df("strong_outflow")):
            scanner.refresh_signal_and_scan()

        exchange.get_ticker.assert_not_called()

    def test_keeps_previous_zscore_when_fetch_fails(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()
        with patch("src.onchain.scanner.fetch_netflow_history", side_effect=RuntimeError("api down")):
            scanner.refresh_signal_and_scan()  # 예외를 전파하지 않아야 함(전체 프로세스 안 죽음)
        assert not scanner.has_position


class TestCheckExits:
    def test_no_op_when_no_position(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()
        scanner.check_exits()
        exchange.get_ticker.assert_not_called()

    def test_closes_position_on_stop_loss(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()
        exchange.get_ohlcv.return_value = [_make_bar(100_000_000) for _ in range(20)]
        exchange.get_ticker.return_value = _make_ticker(100_000_000)
        with patch("src.onchain.scanner.fetch_netflow_history", return_value=_netflow_df("strong_outflow")):
            scanner.refresh_signal_and_scan()
        assert scanner.has_position

        # 진입가 대비 크게 하락한 가격 -> 손절 확실히 이탈
        exchange.get_ticker.return_value = _make_ticker(50_000_000)
        scanner.check_exits()

        assert not scanner.has_position
        assert not portfolio.has_position("BTC/KRW")
        telegram.send_position_closed.assert_called()
