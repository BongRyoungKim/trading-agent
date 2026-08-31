"""Unit tests for src/breakout/scanner.py (wiring layer, exchange/telegram mocked)."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from src.breakout.detector import BreakoutConfig
from src.breakout.exits import ExitConfig
from src.breakout.scanner import BreakoutScanner, ScannerConfig
from src.exchange.models import OHLCVBar, Ticker
from src.portfolio.tracker import PortfolioTracker


def _make_bar(price: float, volume: float = 10.0) -> OHLCVBar:
    from datetime import UTC, datetime

    return OHLCVBar(
        timestamp=datetime.now(UTC),
        open=Decimal(str(price)),
        high=Decimal(str(price)),
        low=Decimal(str(price)),
        close=Decimal(str(price)),
        volume=Decimal(str(volume)),
    )


def _make_scanner(**overrides) -> tuple[BreakoutScanner, MagicMock, MagicMock, PortfolioTracker]:
    exchange = MagicMock()
    telegram = MagicMock()
    portfolio = PortfolioTracker(initial_cash=Decimal("1000000"))
    detector_cfg = overrides.get("detector_cfg") or BreakoutConfig(
        base_window_bars=10, base_range_max_pct=8.0, breakout_margin_pct=1.5,
        vol_mult=5.0, max_move_from_base_low_pct=20.0, min_quote_volume_krw=0.0,
    )
    exit_cfg = overrides.get("exit_cfg") or ExitConfig()
    scanner_cfg = overrides.get("scanner_cfg") or ScannerConfig(max_concurrent_positions=2)
    scanner = BreakoutScanner(
        exchange=exchange,
        telegram=telegram,
        portfolio=portfolio,
        detector_cfg=detector_cfg,
        exit_cfg=exit_cfg,
        scanner_cfg=scanner_cfg,
    )
    return scanner, exchange, telegram, portfolio


class TestScanForEntries:
    def test_opens_paper_position_on_valid_signal(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()
        exchange.get_liquid_symbols.return_value = ["TEST/KRW"]
        # base 10 quiet bars @100, breakout bar @103 with 6x volume
        bars = [_make_bar(100 + (i % 2)) for i in range(10)] + [_make_bar(103, volume=100.0)]
        exchange.get_ohlcv.return_value = bars

        scanner.scan_for_entries()

        assert "TEST/KRW" in scanner.open_symbols
        assert portfolio.has_position("TEST/KRW")
        telegram.send.assert_called()

    def test_skips_symbol_already_held(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()
        portfolio.open_position("TEST/KRW", "buy", Decimal("1"), Decimal("100"))
        exchange.get_liquid_symbols.return_value = ["TEST/KRW"]

        scanner.scan_for_entries()

        exchange.get_ohlcv.assert_not_called()

    def test_stops_at_max_concurrent_positions(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner(
            scanner_cfg=ScannerConfig(max_concurrent_positions=1)
        )
        portfolio.open_position("HELD/KRW", "buy", Decimal("1"), Decimal("100"))
        scanner._breakout_positions["HELD/KRW"] = MagicMock()  # noqa: SLF001 — test setup

        scanner.scan_for_entries()

        exchange.get_liquid_symbols.assert_not_called()

    def test_no_signal_does_not_open_position(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()
        exchange.get_liquid_symbols.return_value = ["TEST/KRW"]
        # Flat quiet bars, no breakout candle at all
        bars = [_make_bar(100) for _ in range(11)]
        exchange.get_ohlcv.return_value = bars

        scanner.scan_for_entries()

        assert scanner.open_symbols == []
        assert not portfolio.has_position("TEST/KRW")

    def test_candidate_scan_failure_does_not_raise(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()
        exchange.get_liquid_symbols.side_effect = RuntimeError("network down")

        scanner.scan_for_entries()  # should not raise

        assert scanner.open_symbols == []


class TestCheckExits:
    def test_stop_loss_closes_position(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner(
            exit_cfg=ExitConfig(trailing_stop_pct=12.0, initial_stop_loss_pct=3.0, time_stop_minutes=60)
        )
        exchange.get_liquid_symbols.return_value = ["TEST/KRW"]
        bars = [_make_bar(100 + (i % 2)) for i in range(10)] + [_make_bar(103, volume=100.0)]
        exchange.get_ohlcv.return_value = bars
        scanner.scan_for_entries()
        assert "TEST/KRW" in scanner.open_symbols

        # price crashes well below the initial 3% stop
        exchange.get_ticker.return_value = Ticker(
            symbol="TEST/KRW", bid=Decimal("90"), ask=Decimal("90"),
            last=Decimal("90"), volume=Decimal("10"),
            timestamp=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

        scanner.check_exits()

        assert "TEST/KRW" not in scanner.open_symbols
        assert not portfolio.has_position("TEST/KRW")
        telegram.send_position_closed.assert_called_once()

    def test_no_exit_keeps_position_open_and_ratchets_stop(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner(
            exit_cfg=ExitConfig(trailing_stop_pct=12.0, initial_stop_loss_pct=3.0, time_stop_minutes=60)
        )
        exchange.get_liquid_symbols.return_value = ["TEST/KRW"]
        bars = [_make_bar(100 + (i % 2)) for i in range(10)] + [_make_bar(103, volume=100.0)]
        exchange.get_ohlcv.return_value = bars
        scanner.scan_for_entries()

        import datetime as _dt
        exchange.get_ticker.return_value = Ticker(
            symbol="TEST/KRW", bid=Decimal("200"), ask=Decimal("200"),
            last=Decimal("200"), volume=Decimal("10"),
            timestamp=_dt.datetime.now(_dt.UTC),
        )

        scanner.check_exits()

        assert "TEST/KRW" in scanner.open_symbols
        bpos = scanner._breakout_positions["TEST/KRW"]  # noqa: SLF001
        assert bpos.highest_price == Decimal("200")
        assert bpos.stop_loss == Decimal("200") * (1 - Decimal("12.0") / 100)
        telegram.send_position_closed.assert_not_called()

    def test_ticker_fetch_failure_does_not_raise(self) -> None:
        scanner, exchange, telegram, portfolio = _make_scanner()
        exchange.get_liquid_symbols.return_value = ["TEST/KRW"]
        bars = [_make_bar(100 + (i % 2)) for i in range(10)] + [_make_bar(103, volume=100.0)]
        exchange.get_ohlcv.return_value = bars
        scanner.scan_for_entries()

        exchange.get_ticker.side_effect = RuntimeError("network down")

        scanner.check_exits()  # should not raise

        assert "TEST/KRW" in scanner.open_symbols
