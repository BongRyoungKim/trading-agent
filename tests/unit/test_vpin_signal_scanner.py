"""Unit tests for src/vpin_signal/scanner.py (wiring layer, exchange/telegram/binance mocked)."""
from __future__ import annotations

import types
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from src.exchange.models import OHLCVBar, Ticker
from src.risk.manager import PortfolioState, RiskManager
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

    def test_default_vpin_quantile_is_2026_10_25_backtest_value(self) -> None:
        # 2026-10-25 백테스트(scripts/vpin_backtest.py)에서 검증된 값(0.93)
        # 으로 기본값이 바뀌었는지 — 회귀 방지.
        assert VPINScannerConfig().vpin_quantile == 0.93

    def test_stricter_vpin_quantile_can_suppress_a_borderline_entry(self) -> None:
        # 완만한 쏠림 -- 기본 quantile(0.93)에서는 진입하지만 더 엄격한
        # quantile(1.0)로 설정하면 같은 데이터에서도 진입하지 않아야 한다
        # (scanner_cfg.vpin_quantile이 실제로 latest_row()에 전달되는지 확인).
        rng = np.random.default_rng(0)
        n = MIN_BARS_REQUIRED + 10
        timestamps = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
        volumes = np.full(n, 1000.0) + rng.normal(0, 1, n)
        deltas = rng.normal(0, 1, n)
        df = pd.DataFrame({"timestamp": timestamps, "volume": volumes, "delta": deltas})
        df.loc[df.index[-WINDOW:], "delta"] = 1200.0
        df.loc[df.index[-WINDOW:], "volume"] = 1300.0

        scanner, exchange, telegram, portfolio, fetcher = _make_scanner(
            scanner_cfg=VPINScannerConfig(
                symbol_pairs=(("BTC/KRW", "BTCUSDT"), ("ETH/KRW", "ETHUSDT")),
                position_size_krw=Decimal("100000"), max_concurrent_positions=2,
                vpin_quantile=1.0,
            )
        )
        fetcher.return_value = df
        exchange.get_ohlcv.return_value = [_make_bar(1000.0) for _ in range(20)]
        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 1000.0)

        scanner.refresh_signal_and_scan()

        assert scanner.open_symbols == []


def _make_risk_manager(initial_capital=Decimal("1000000"), **kwargs) -> RiskManager:
    settings = types.SimpleNamespace(max_drawdown_halt=0.15, max_daily_loss=0.05, max_open_positions=100)
    state = PortfolioState(capital=initial_capital, peak_capital=initial_capital)
    return RiskManager(settings, state, **kwargs)


class TestRiskManagerIntegration:
    """2026-10-12 추가 — 실거래 전환 전 필수 안전장치(사용자 요청).
    risk_manager=None(기본값)이면 기존 동작 그대로라는 건 다른 모든 테스트가
    이미 보장한다(이 클래스 추가 전부터 전부 통과)."""

    def test_drawdown_halt_blocks_new_entries(self) -> None:
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner()
        risk_manager = _make_risk_manager(initial_capital=Decimal("1000000"))
        risk_manager._state.capital = Decimal("800000")  # -20% drawdown > 15% halt
        scanner._risk_manager = risk_manager
        fetcher.return_value = _make_binance_df(MIN_BARS_REQUIRED + 10, bullish=True)

        scanner.refresh_signal_and_scan()

        assert scanner.open_symbols == []
        telegram.send_risk_alert.assert_called_once()

    def test_drawdown_halt_only_alerts_once_while_still_blocked(self) -> None:
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner()
        risk_manager = _make_risk_manager()
        risk_manager._state.capital = Decimal("800000")
        scanner._risk_manager = risk_manager
        fetcher.return_value = _make_binance_df(MIN_BARS_REQUIRED + 10, bullish=True)

        scanner.refresh_signal_and_scan()
        scanner.refresh_signal_and_scan()
        scanner.refresh_signal_and_scan()

        telegram.send_risk_alert.assert_called_once()  # 매 틱 스팸 방지

    def test_cooldown_blocks_new_entries_after_consecutive_losses(self) -> None:
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner()
        risk_manager = _make_risk_manager(consecutive_loss_limit=1, consecutive_loss_cooldown_minutes=720)
        scanner._risk_manager = risk_manager
        risk_manager.on_position_closed(Decimal("-1000"))  # 1회 손실 -> 즉시 쿨다운(한도=1)
        fetcher.return_value = _make_binance_df(MIN_BARS_REQUIRED + 10, bullish=True)

        scanner.refresh_signal_and_scan()

        assert scanner.open_symbols == []

    def test_opening_position_calls_on_position_opened(self) -> None:
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner()
        risk_manager = _make_risk_manager()
        scanner._risk_manager = risk_manager
        fetcher.return_value = _make_binance_df(MIN_BARS_REQUIRED + 10, bullish=True)
        exchange.get_ohlcv.return_value = [_make_bar(1000.0) for _ in range(20)]
        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 1000.0)

        scanner.refresh_signal_and_scan()

        assert risk_manager._state.open_positions == len(scanner.open_symbols) > 0

    def test_closing_position_at_a_loss_updates_consecutive_loss_count(self) -> None:
        # 심볼 1개로 제한 -- 아니면 두 심볼이 동시에 열려서 둘 다 손절될 때
        # consecutive_losses가 1이 아니라 2가 되어버림(get_ticker 모의객체가
        # 심볼 구분 없이 전역 반환값 하나만 쓰기 때문).
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner(
            scanner_cfg=VPINScannerConfig(
                symbol_pairs=(("BTC/KRW", "BTCUSDT"),),
                position_size_krw=Decimal("100000"), max_concurrent_positions=1,
            )
        )
        risk_manager = _make_risk_manager(consecutive_loss_limit=5)  # 이번엔 안 걸리게 넉넉히
        scanner._risk_manager = risk_manager
        fetcher.return_value = _make_binance_df(MIN_BARS_REQUIRED + 10, bullish=True)
        exchange.get_ohlcv.return_value = [_make_bar(1000.0) for _ in range(20)]
        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 1000.0)
        scanner.refresh_signal_and_scan()
        opened_symbol = scanner.open_symbols[0]

        exchange.get_ticker.return_value = _make_ticker(opened_symbol, 500.0)  # 큰 폭 하락 -> 손절
        scanner.check_exits()

        assert risk_manager._state.consecutive_losses == 1

    def test_cooldown_newly_triggered_sends_risk_alert(self) -> None:
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner()
        risk_manager = _make_risk_manager(consecutive_loss_limit=1, consecutive_loss_cooldown_minutes=720)
        scanner._risk_manager = risk_manager
        fetcher.return_value = _make_binance_df(MIN_BARS_REQUIRED + 10, bullish=True)
        exchange.get_ohlcv.return_value = [_make_bar(1000.0) for _ in range(20)]
        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 1000.0)
        scanner.refresh_signal_and_scan()
        opened_symbol = scanner.open_symbols[0]

        exchange.get_ticker.return_value = _make_ticker(opened_symbol, 500.0)  # 손절 -> 연속손실 1회 -> 한도 도달
        scanner.check_exits()

        telegram.send_risk_alert.assert_called_once()

    def test_no_risk_manager_still_opens_and_closes_normally(self) -> None:
        # risk_manager=None(기본값)일 때 안전장치 추가 전과 완전히 동일하게
        # 동작해야 한다 -- 배포된 버전과의 하위호환성 명시적 확인.
        scanner, exchange, telegram, portfolio, fetcher = _make_scanner()
        assert scanner._risk_manager is None
        fetcher.return_value = _make_binance_df(MIN_BARS_REQUIRED + 10, bullish=True)
        exchange.get_ohlcv.return_value = [_make_bar(1000.0) for _ in range(20)]
        exchange.get_ticker.return_value = _make_ticker("BTC/KRW", 1000.0)

        scanner.refresh_signal_and_scan()

        assert len(scanner.open_symbols) > 0


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
