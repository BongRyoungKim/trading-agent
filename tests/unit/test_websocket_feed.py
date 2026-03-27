"""Unit tests for BinanceWebSocketFeed."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.exchange.websocket_feed import (
    BinanceWebSocketFeed,
    _normalise_symbol,
    _ws_read_frame,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_kline_msg(symbol: str = "BTCUSDT", closed: bool = True, close: str = "50000") -> str:
    return json.dumps({
        "stream": f"{symbol.lower()}@kline_1m",
        "data": {
            "e": "kline",
            "s": symbol,
            "k": {
                "t": 1700000000000,
                "T": 1700000059999,
                "s": symbol,
                "i": "1m",
                "o": "49000",
                "h": "51000",
                "l": "48500",
                "c": close,
                "v": "100.5",
                "x": closed,
            },
        },
    })


def _make_feed(callback=None) -> BinanceWebSocketFeed:
    cb = callback or MagicMock()
    return BinanceWebSocketFeed(
        symbols=["BTC/USDT"],
        interval="1m",
        on_candle_close=cb,
    )


# ── Symbol normalisation ──────────────────────────────────────────────────────

class TestNormaliseSymbol:
    def test_btcusdt(self) -> None:
        assert _normalise_symbol("BTCUSDT") == "BTC/USDT"

    def test_ethbtc(self) -> None:
        assert _normalise_symbol("ETHBTC") == "ETH/BTC"

    def test_bnbusdt(self) -> None:
        assert _normalise_symbol("BNBUSDT") == "BNB/USDT"

    def test_solusdc(self) -> None:
        assert _normalise_symbol("SOLUSDC") == "SOL/USDC"

    def test_unknown_returns_raw(self) -> None:
        assert _normalise_symbol("XYZXYZ") == "XYZXYZ"

    def test_lowercase_input(self) -> None:
        assert _normalise_symbol("btcusdt") == "BTC/USDT"


# ── Init ──────────────────────────────────────────────────────────────────────

class TestInit:
    def test_symbols_normalised_to_lowercase(self) -> None:
        feed = _make_feed()
        assert "btcusdt" in feed._symbols  # noqa: SLF001

    def test_slash_removed_from_symbol(self) -> None:
        feed = BinanceWebSocketFeed(["BTC/USDT", "ETH/USDT"], "1m", MagicMock())
        assert feed._symbols == ["btcusdt", "ethusdt"]  # noqa: SLF001

    def test_not_running_before_start(self) -> None:
        feed = _make_feed()
        assert not feed.is_running


# ── start / stop ──────────────────────────────────────────────────────────────

class TestStartStop:
    def test_start_creates_daemon_thread(self) -> None:
        feed = _make_feed()
        with patch.object(feed, "_run_loop"):
            feed.start()
            assert feed._thread is not None  # noqa: SLF001
            assert feed._thread.daemon is True  # noqa: SLF001

    def test_stop_sets_stop_event(self) -> None:
        feed = _make_feed()
        assert not feed._stop_event.is_set()  # noqa: SLF001
        feed.stop()
        assert feed._stop_event.is_set()  # noqa: SLF001


# ── Message handling ──────────────────────────────────────────────────────────

class TestHandleMessage:
    def test_closed_candle_triggers_callback(self) -> None:
        cb = MagicMock()
        feed = _make_feed(callback=cb)
        feed._handle_message(_make_kline_msg(closed=True))  # noqa: SLF001
        cb.assert_called_once()
        symbol, candle = cb.call_args[0]
        assert symbol == "BTC/USDT"
        assert candle["close"] == 50000.0

    def test_open_candle_does_not_trigger_callback(self) -> None:
        cb = MagicMock()
        feed = _make_feed(callback=cb)
        feed._handle_message(_make_kline_msg(closed=False))  # noqa: SLF001
        cb.assert_not_called()

    def test_non_kline_event_ignored(self) -> None:
        cb = MagicMock()
        feed = _make_feed(callback=cb)
        msg = json.dumps({"data": {"e": "trade", "p": "50000"}})
        feed._handle_message(msg)  # noqa: SLF001
        cb.assert_not_called()

    def test_invalid_json_does_not_raise(self) -> None:
        feed = _make_feed()
        feed._handle_message("not valid json {{{")  # noqa: SLF001  # should not raise

    def test_candle_fields_parsed_correctly(self) -> None:
        cb = MagicMock()
        feed = _make_feed(callback=cb)
        feed._handle_message(_make_kline_msg(close="42000.5"))  # noqa: SLF001
        _, candle = cb.call_args[0]
        assert candle["open"] == 49000.0
        assert candle["high"] == 51000.0
        assert candle["low"] == 48500.0
        assert candle["close"] == 42000.5
        assert candle["volume"] == 100.5

    def test_bytes_message_decoded(self) -> None:
        cb = MagicMock()
        feed = _make_feed(callback=cb)
        msg_bytes = _make_kline_msg(closed=True).encode("utf-8")
        feed._handle_message(msg_bytes)  # noqa: SLF001
        cb.assert_called_once()

    def test_multiple_symbols(self) -> None:
        """Each symbol triggers its own callback."""
        cb = MagicMock()
        feed = BinanceWebSocketFeed(["BTC/USDT", "ETH/USDT"], "1m", cb)
        feed._handle_message(_make_kline_msg("BTCUSDT", closed=True))  # noqa: SLF001
        feed._handle_message(_make_kline_msg("ETHUSDT", closed=True))  # noqa: SLF001
        assert cb.call_count == 2
        symbols = [call[0][0] for call in cb.call_args_list]
        assert "BTC/USDT" in symbols
        assert "ETH/USDT" in symbols


# ── Dashboard state new endpoints ─────────────────────────────────────────────

class TestDashboardStateNewMethods:
    def _make_state_with_engine(self) -> object:
        from src.dashboard.state import DashboardState
        from decimal import Decimal
        from datetime import UTC, datetime

        state = DashboardState()
        eng = MagicMock()
        eng.mode = "paper"
        eng.is_paused = False
        eng.circuit_breaker.state.name = "CLOSED"
        eng.market_hours.enabled = False
        eng.market_hours.trading_hours = "00:00-23:59"
        eng.market_hours.trading_days = "mon-sun"
        eng._portfolio.pnl_report.return_value = {  # noqa: SLF001
            "cash": 10000.0, "realized_pnl": 0.0, "open_positions": 0
        }
        eng._portfolio.open_symbols.return_value = []  # noqa: SLF001

        # Build real TradeJournal with fake trades
        from src.portfolio.journal import TradeJournal, TradeRecord
        journal = TradeJournal()
        journal.record(TradeRecord(
            symbol="BTC/USDT",
            side="buy",
            amount=Decimal("0.01"),
            entry_price=Decimal("50000"),
            exit_price=Decimal("52000"),
            entry_time=datetime(2024, 1, 1, tzinfo=UTC),
            exit_time=datetime(2024, 1, 2, tzinfo=UTC),
            pnl=Decimal("20"),
            commission=Decimal("1"),
            reason="signal",
        ))
        eng.journal = journal
        state.register_engine(eng)
        return state

    def test_get_trades_returns_list(self) -> None:
        state = self._make_state_with_engine()
        trades = state.get_trades()
        assert len(trades) == 1
        assert trades[0]["symbol"] == "BTC/USDT"
        assert trades[0]["is_win"] is True

    def test_get_stats_returns_dict(self) -> None:
        state = self._make_state_with_engine()
        stats = state.get_stats()
        assert stats["total_trades"] == 1
        assert stats["wins"] == 1
        assert stats["win_rate_pct"] == 100.0

    def test_get_equity_curve_returns_cumulative_pnl(self) -> None:
        state = self._make_state_with_engine()
        curve = state.get_equity_curve()
        assert len(curve) == 1
        assert curve[0]["pnl"] == 20.0

    def test_get_trades_no_engine(self) -> None:
        from src.dashboard.state import DashboardState
        state = DashboardState()
        assert state.get_trades() == []

    def test_get_stats_no_engine(self) -> None:
        from src.dashboard.state import DashboardState
        state = DashboardState()
        assert state.get_stats() == {}

    def test_get_equity_no_engine(self) -> None:
        from src.dashboard.state import DashboardState
        state = DashboardState()
        assert state.get_equity_curve() == []
