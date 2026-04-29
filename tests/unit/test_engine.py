"""Unit tests for the TradingEngine."""
from __future__ import annotations

import threading
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from datetime import UTC, datetime, timedelta

from src.engine import TradingEngine
from src.risk.manager import PortfolioState, RiskManager
from src.strategy.models import Signal, SignalAction


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_settings(mode: str = "paper") -> MagicMock:
    s = MagicMock()
    s.trading_mode = mode
    s.max_position_risk = 0.02
    s.max_open_positions = 5
    s.max_daily_loss = 0.05
    s.max_drawdown_halt = 0.15
    return s


def _make_ticker(price: float = 50000.0) -> MagicMock:
    t = MagicMock()
    t.last = Decimal(str(price))
    return t


def _make_signal(action: SignalAction = SignalAction.HOLD) -> Signal:
    return Signal(
        symbol="BTC/USDT",
        action=action,
        strength=0.8,
        reason="test",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _make_dataframe(n: int = 50) -> pd.DataFrame:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    index = pd.DatetimeIndex([base + timedelta(hours=i) for i in range(n)])
    return pd.DataFrame(
        {
            "open":   [50000.0] * n,
            "high":   [50500.0] * n,
            "low":    [49500.0] * n,
            "close":  [50000.0] * n,
            "volume": [100.0] * n,
        },
        index=index,
    )


@pytest.fixture()
def settings():
    return _make_settings("paper")


@pytest.fixture()
def exchange():
    ex = MagicMock()
    ex.get_ohlcv_dataframe.return_value = _make_dataframe()
    ex.get_ticker.return_value = _make_ticker(50000.0)
    return ex


@pytest.fixture()
def strategy():
    strat = MagicMock()
    strat.generate_signal.return_value = _make_signal(SignalAction.HOLD)
    strat.min_required_bars.return_value = 1
    strat.timeframe = "1h"
    return strat


@pytest.fixture()
def risk_manager(settings):
    state = PortfolioState(
        capital=Decimal("10000"),
        peak_capital=Decimal("10000"),
    )
    return RiskManager(settings, state)


def _make_position_mock(entry: float = 50000.0, amount: float = 1.0,
                         stop_loss=None, take_profit=None,
                         entry_time: datetime | None = None):
    """
    amount defaults to 1.0 so that amount*price >> 5001 KRW dust threshold.
    entry_time defaults to 1 minute ago so 45-min time-stop does not trigger
    unless explicitly set to an older time.
    """
    pos = MagicMock()
    pos.entry_price = Decimal(str(entry))
    pos.amount = Decimal(str(amount))
    pos.stop_loss = stop_loss
    pos.take_profit = take_profit
    pos.side = "buy"
    pos.trailing_stop_pct = None
    pos.entry_time = entry_time if entry_time is not None else (
        datetime.now(UTC) - timedelta(minutes=1)
    )
    return pos


@pytest.fixture()
def portfolio():
    p = MagicMock()
    p.cash = Decimal("10000")
    p.has_position.return_value = False
    p.get_position.return_value = _make_position_mock()
    p.snapshot.return_value = MagicMock()
    p.pnl_report.return_value = {
        "cash": "10000",
        "realized_pnl": "0",
        "unrealized_pnl": "0",
        "open_positions": "0",
        "open_symbols": "[]",
        "total_commission": "0",
    }
    return p


@pytest.fixture()
def telegram():
    t = MagicMock()
    t.is_enabled = False  # prevents daily-report scheduler job in lifecycle tests
    return t


@pytest.fixture()
def engine(settings, exchange, strategy, risk_manager, portfolio, telegram):
    return TradingEngine(settings, exchange, strategy, risk_manager, portfolio, telegram)


# ── Init ──────────────────────────────────────────────────────────────────────

class TestTradingEngineInit:
    def test_paper_mode_by_default(self, engine):
        assert engine.mode == "paper"

    def test_live_mode_when_settings_say_live(
        self, exchange, strategy, risk_manager, portfolio, telegram
    ):
        s = _make_settings("live")
        eng = TradingEngine(s, exchange, strategy, risk_manager, portfolio, telegram)
        assert eng.mode == "live"

    def test_no_telegram_creates_disabled_client(
        self, settings, exchange, strategy, risk_manager, portfolio
    ):
        eng = TradingEngine(settings, exchange, strategy, risk_manager, portfolio)
        assert eng._telegram.is_enabled is False


# ── Tick ──────────────────────────────────────────────────────────────────────

class TestTradingEngineTick:
    def test_tick_delegates_to_process_symbol(self, engine):
        with patch.object(engine, "_process_symbol") as mock_process:
            engine.tick("BTC/USDT")
        mock_process.assert_called_once_with("BTC/USDT")

    def test_tick_catches_exceptions_and_notifies(self, engine):
        with patch.object(engine, "_process_symbol", side_effect=RuntimeError("boom")):
            engine.tick("BTC/USDT")  # must not raise
        engine._telegram.send_error.assert_called_once()
        args = engine._telegram.send_error.call_args[0]
        assert "boom" in args[0]


# ── Process Symbol ────────────────────────────────────────────────────────────

class TestProcessSymbol:
    def test_hold_signal_does_nothing(self, engine, strategy, portfolio):
        strategy.generate_signal.return_value = _make_signal(SignalAction.HOLD)
        with patch.object(engine, "_open_position") as mock_open, \
             patch.object(engine, "_close_position") as mock_close:
            engine._process_symbol("BTC/USDT")
        mock_open.assert_not_called()
        mock_close.assert_not_called()

    def test_buy_signal_without_position_opens(self, engine, strategy, portfolio):
        strategy.generate_signal.return_value = _make_signal(SignalAction.BUY)
        portfolio.has_position.return_value = False
        with patch.object(engine, "_open_position") as mock_open:
            engine._process_symbol("BTC/USDT")
        mock_open.assert_called_once()
        assert mock_open.call_args[0][0] == "BTC/USDT"

    def test_buy_signal_with_existing_position_does_nothing(self, engine, strategy, portfolio):
        strategy.generate_signal.return_value = _make_signal(SignalAction.BUY)
        portfolio.has_position.return_value = True
        with patch.object(engine, "_open_position") as mock_open:
            engine._process_symbol("BTC/USDT")
        mock_open.assert_not_called()

    def test_sell_signal_with_position_closes(self, engine, strategy, portfolio):
        strategy.generate_signal.return_value = _make_signal(SignalAction.SELL)
        portfolio.has_position.return_value = True
        # Position must be older than 10-min minimum hold time for SELL to trigger
        pos = _make_position_mock(entry_time=datetime.now(UTC) - timedelta(minutes=35))
        portfolio.get_position.return_value = pos
        with patch.object(engine, "_close_position") as mock_close:
            engine._process_symbol("BTC/USDT")
        mock_close.assert_called_once()
        assert mock_close.call_args[0][0] == "BTC/USDT"

    def test_sell_signal_without_position_does_nothing(self, engine, strategy, portfolio):
        strategy.generate_signal.return_value = _make_signal(SignalAction.SELL)
        portfolio.has_position.return_value = False
        with patch.object(engine, "_close_position") as mock_close:
            engine._process_symbol("BTC/USDT")
        mock_close.assert_not_called()


# ── Open Position ─────────────────────────────────────────────────────────────

class TestOpenPosition:
    def test_paper_open_updates_portfolio(self, engine, exchange, portfolio):
        exchange.get_ticker.return_value = _make_ticker(50000.0)
        engine._open_position("BTC/USDT")
        portfolio.open_position.assert_called_once()
        call_kwargs = portfolio.open_position.call_args[1]
        assert call_kwargs["symbol"] == "BTC/USDT"
        assert call_kwargs["side"] == "buy"

    def test_paper_open_sends_telegram(self, engine, exchange, portfolio, telegram):
        exchange.get_ticker.return_value = _make_ticker(50000.0)
        engine._open_position("BTC/USDT")
        telegram.send_order_filled.assert_called_once()

    def test_risk_block_prevents_open(self, engine, exchange, portfolio, telegram):
        from src.utils.exceptions import MaxDrawdownExceededError
        exchange.get_ticker.return_value = _make_ticker(50000.0)
        with patch.object(engine._risk_manager, "check_can_open_position",
                          side_effect=MaxDrawdownExceededError("drawdown")):
            engine._open_position("BTC/USDT")
        portfolio.open_position.assert_not_called()
        telegram.send_risk_alert.assert_called_once()

    def test_zero_amount_skips_open(self, engine, exchange, portfolio):
        # If stop_loss == entry_price, fixed_fraction returns 0
        exchange.get_ticker.return_value = _make_ticker(50000.0)
        with patch("src.engine.fixed_fraction", return_value=Decimal("0")):
            engine._open_position("BTC/USDT")
        portfolio.open_position.assert_not_called()

    def test_live_mode_calls_exchange_place_order(
        self, settings, exchange, strategy, risk_manager, portfolio, telegram
    ):
        s = _make_settings("live")
        eng = TradingEngine(s, exchange, strategy, risk_manager, portfolio, telegram)
        order = MagicMock()
        order.price = Decimal("50000")
        order.fee = 5.0
        exchange.get_ticker.return_value = _make_ticker(50000.0)
        exchange.place_order.return_value = order
        eng._open_position("BTC/USDT")
        exchange.place_order.assert_called_once()
        call_args = exchange.place_order.call_args[0]
        assert call_args[0] == "BTC/USDT"
        assert call_args[1] == "buy"
        assert isinstance(call_args[2], Decimal)  # amount


# ── Close Position ────────────────────────────────────────────────────────────

class TestClosePosition:
    def _make_position(self, entry: float = 50000.0, amount: float = 0.01):
        pos = MagicMock()
        pos.entry_price = Decimal(str(entry))
        pos.amount = Decimal(str(amount))
        return pos

    def test_paper_close_updates_portfolio(self, engine, exchange, portfolio):
        pos = self._make_position()
        portfolio.get_position.return_value = pos
        exchange.get_ticker.return_value = _make_ticker(55000.0)
        portfolio.close_position.return_value = Decimal("50")
        engine._close_position("BTC/USDT")
        portfolio.close_position.assert_called_once()

    def test_paper_close_sends_telegram(self, engine, exchange, portfolio, telegram):
        pos = self._make_position()
        portfolio.get_position.return_value = pos
        exchange.get_ticker.return_value = _make_ticker(55000.0)
        portfolio.close_position.return_value = Decimal("50")
        engine._close_position("BTC/USDT")
        telegram.send_position_closed.assert_called_once()

    def test_close_returns_early_if_no_position(self, engine, portfolio):
        portfolio.get_position.return_value = None
        engine._close_position("BTC/USDT")
        portfolio.close_position.assert_not_called()

    def test_close_updates_risk_manager(self, engine, exchange, portfolio):
        pos = self._make_position()
        portfolio.get_position.return_value = pos
        exchange.get_ticker.return_value = _make_ticker(55000.0)
        pnl = Decimal("50")
        portfolio.close_position.return_value = pnl
        with patch.object(engine._risk_manager, "on_position_closed") as mock_closed:
            engine._close_position("BTC/USDT")
        mock_closed.assert_called_once_with(pnl)

    def test_live_mode_calls_exchange_place_order(
        self, settings, exchange, strategy, risk_manager, portfolio, telegram
    ):
        s = _make_settings("live")
        eng = TradingEngine(s, exchange, strategy, risk_manager, portfolio, telegram)
        pos = self._make_position()
        portfolio.get_position.return_value = pos
        exchange.get_ticker.return_value = _make_ticker(55000.0)
        order = MagicMock()
        order.price = Decimal("55000")
        order.fee = None
        exchange.place_order.return_value = order
        portfolio.close_position.return_value = Decimal("50")
        eng._close_position("BTC/USDT")
        exchange.place_order.assert_called_once()
        call_args = exchange.place_order.call_args[0]
        assert call_args[1] == "sell"


# ── Lifecycle ─────────────────────────────────────────────────────────────────

class TestTradingEngineLifecycle:
    def test_stop_sets_stop_event(self, engine):
        assert not engine._stop_event.is_set()
        engine.stop()
        assert engine._stop_event.is_set()

    def test_start_blocks_until_stop(self, engine):
        """start() should block and unblock when stop() is called from another thread."""
        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        engine._scheduler = mock_scheduler

        with patch.object(engine, "_register_signal_handlers"):
            started = threading.Event()

            def _run():
                started.set()
                engine.start(["BTC/USDT"], interval_seconds=60, daily_report_hour=None)

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            started.wait(timeout=1)
            engine.stop()
            t.join(timeout=2)

        assert not t.is_alive()

    def test_add_job_called_per_symbol(self, engine):
        symbols = ["BTC/USDT", "ETH/USDT"]
        mock_scheduler = MagicMock()
        mock_scheduler.running = False  # skip shutdown wait
        engine._scheduler = mock_scheduler

        with patch.object(engine, "_register_signal_handlers"):
            threading.Thread(target=engine.stop, daemon=True).start()
            engine.start(symbols, interval_seconds=30, daily_report_hour=None,
                         heartbeat_interval=None)

        # At least one add_job call per symbol (additional jobs may be scheduled
        # for symbol refresh, heartbeat, etc.)
        assert mock_scheduler.add_job.call_count >= len(symbols)

    def test_daily_report_job_added_when_telegram_enabled(
        self, settings, exchange, strategy, risk_manager, portfolio
    ):
        telegram = MagicMock()
        telegram.is_enabled = True
        engine = TradingEngine(settings, exchange, strategy, risk_manager, portfolio, telegram)
        mock_scheduler = MagicMock()
        mock_scheduler.running = False
        engine._scheduler = mock_scheduler

        with patch.object(engine, "_register_signal_handlers"):
            threading.Thread(target=engine.stop, daemon=True).start()
            engine.start(["BTC/USDT"], interval_seconds=60, daily_report_hour=0)

        job_ids = [call[1].get("id") for call in mock_scheduler.add_job.call_args_list]
        assert "daily_report" in job_ids

    def test_daily_report_job_not_added_when_telegram_disabled(self, engine):
        mock_scheduler = MagicMock()
        mock_scheduler.running = False
        engine._scheduler = mock_scheduler

        with patch.object(engine, "_register_signal_handlers"):
            threading.Thread(target=engine.stop, daemon=True).start()
            engine.start(["BTC/USDT"], interval_seconds=60, daily_report_hour=0)

        job_ids = [call[1].get("id") for call in mock_scheduler.add_job.call_args_list]
        assert "daily_report" not in job_ids


# ── Stop-loss / Take-profit ───────────────────────────────────────────────────

class TestStopLossAndTakeProfit:
    def test_stop_loss_triggers_close(self, engine, exchange, portfolio):
        pos = _make_position_mock(entry=50000.0, stop_loss=Decimal("45000"))
        portfolio.has_position.return_value = True
        portfolio.get_position.return_value = pos
        exchange.get_ticker.return_value = _make_ticker(44000.0)  # below stop

        with patch.object(engine, "_close_position") as mock_close, \
             patch.object(engine, "_open_position") as mock_open:
            engine._process_symbol("BTC/USDT")

        mock_close.assert_called_once()
        call_kwargs = mock_close.call_args[1]
        assert call_kwargs["reason"] == "stop_loss"
        mock_open.assert_not_called()

    def test_stop_loss_not_triggered_when_price_above(self, engine, exchange, portfolio):
        pos = _make_position_mock(entry=50000.0, stop_loss=Decimal("45000"))
        portfolio.has_position.return_value = True
        portfolio.get_position.return_value = pos
        exchange.get_ticker.return_value = _make_ticker(50000.0)  # above stop
        engine._strategy_provider.generate_signal.return_value = _make_signal(SignalAction.HOLD)

        with patch.object(engine, "_close_position") as mock_close:
            engine._process_symbol("BTC/USDT")

        # Not called due to stop-loss; HOLD signal means no strategy close either
        mock_close.assert_not_called()

    def test_take_profit_triggers_close(self, engine, exchange, portfolio):
        pos = _make_position_mock(entry=50000.0, take_profit=Decimal("60000"))
        portfolio.has_position.return_value = True
        portfolio.get_position.return_value = pos
        exchange.get_ticker.return_value = _make_ticker(62000.0)  # above take-profit

        with patch.object(engine, "_close_position") as mock_close:
            engine._process_symbol("BTC/USDT")

        mock_close.assert_called_once()
        call_kwargs = mock_close.call_args[1]
        assert call_kwargs["reason"] == "take_profit"

    def test_no_stop_loss_field_does_not_close(self, engine, exchange, portfolio):
        pos = _make_position_mock(entry=50000.0, stop_loss=None, take_profit=None)
        portfolio.has_position.return_value = True
        portfolio.get_position.return_value = pos
        exchange.get_ticker.return_value = _make_ticker(30000.0)  # way below entry
        engine._strategy_provider.generate_signal.return_value = _make_signal(SignalAction.HOLD)

        with patch.object(engine, "_close_position") as mock_close:
            engine._process_symbol("BTC/USDT")

        mock_close.assert_not_called()


# ── Strategy resolution ───────────────────────────────────────────────────────

def _mock_strat() -> MagicMock:
    """Return a strategy mock with required attributes for validation."""
    s = MagicMock()
    s.generate_signal.return_value = _make_signal(SignalAction.HOLD)
    s.min_required_bars.return_value = 1
    s.timeframe = "1h"
    return s


class TestStrategyResolution:
    def test_dict_strategy_resolved_by_symbol(
        self, settings, exchange, risk_manager, portfolio, telegram
    ):
        btc_strat = _mock_strat()
        eth_strat = _mock_strat()
        portfolio.has_position.return_value = False

        engine = TradingEngine(
            settings, exchange,
            strategy={"BTC/USDT": btc_strat, "ETH/USDT": eth_strat},
            risk_manager=risk_manager, portfolio=portfolio, telegram=telegram,
        )
        engine._process_symbol("BTC/USDT")
        btc_strat.generate_signal.assert_called_once()
        eth_strat.generate_signal.assert_not_called()

    def test_dict_strategy_dispatches_correctly_for_each_symbol(
        self, settings, exchange, risk_manager, portfolio, telegram
    ):
        """Each symbol in a dict gets its own strategy instance."""
        btc_strat = _mock_strat()
        eth_strat = _mock_strat()
        portfolio.has_position.return_value = False

        eng = TradingEngine(
            settings, exchange,
            strategy={"BTC/USDT": btc_strat, "ETH/USDT": eth_strat},
            risk_manager=risk_manager, portfolio=portfolio, telegram=telegram,
        )
        eng._process_symbol("ETH/USDT")
        eth_strat.generate_signal.assert_called_once()
        btc_strat.generate_signal.assert_not_called()

    def test_dict_fallback_to_first_when_symbol_missing(
        self, settings, exchange, risk_manager, portfolio, telegram
    ):
        only_strat = _mock_strat()
        portfolio.has_position.return_value = False

        engine = TradingEngine(
            settings, exchange,
            strategy={"BTC/USDT": only_strat},
            risk_manager=risk_manager, portfolio=portfolio, telegram=telegram,
        )
        engine._process_symbol("ETH/USDT")  # not in dict
        only_strat.generate_signal.assert_called_once()


# ── Journal integration ───────────────────────────────────────────────────────

class TestJournalIntegration:
    def _make_position(self, entry: float = 50000.0, amount: float = 0.01):
        from datetime import UTC, datetime
        pos = MagicMock()
        pos.entry_price = Decimal(str(entry))
        pos.amount = Decimal(str(amount))
        pos.side = "buy"
        pos.entry_time = datetime(2024, 1, 1, tzinfo=UTC)
        return pos

    def test_close_position_records_trade(self, engine, exchange, portfolio):
        pos = self._make_position()
        portfolio.get_position.return_value = pos
        exchange.get_ticker.return_value = _make_ticker(55000.0)
        portfolio.close_position.return_value = Decimal("50")

        assert engine.journal.total_trades == 0
        engine._close_position("BTC/USDT")
        assert engine.journal.total_trades == 1

    def test_journal_records_reason(self, engine, exchange, portfolio):
        pos = self._make_position()
        portfolio.get_position.return_value = pos
        exchange.get_ticker.return_value = _make_ticker(45000.0)
        portfolio.close_position.return_value = Decimal("-50")

        engine._close_position("BTC/USDT", reason="stop_loss")
        assert engine.journal.trades[0].reason == "stop_loss"

    def test_journal_win_rate_after_multiple_closes(self, engine, exchange, portfolio):
        pos = self._make_position()
        portfolio.get_position.return_value = pos
        exchange.get_ticker.return_value = _make_ticker(55000.0)

        portfolio.close_position.return_value = Decimal("100")
        engine._close_position("BTC/USDT")
        portfolio.close_position.return_value = Decimal("100")
        engine._close_position("BTC/USDT")
        portfolio.close_position.return_value = Decimal("-50")
        engine._close_position("BTC/USDT")

        stats = engine.journal.stats()
        assert stats["total_trades"] == 3
        assert stats["wins"] == 2
        assert abs(stats["win_rate_pct"] - 66.67) < 0.01

    def test_no_position_does_not_record(self, engine, portfolio):
        portfolio.get_position.return_value = None
        engine._close_position("BTC/USDT")
        assert engine.journal.total_trades == 0


# ── Reconciliation ────────────────────────────────────────────────────────────

class TestReconciliation:
    def test_reconcile_logs_no_ghost_for_tracked_symbols(self, engine, exchange):
        exchange.get_ticker.return_value = _make_ticker(50000.0)
        engine._portfolio.open_symbols.return_value = []
        # Should not raise
        engine._reconcile_positions(["BTC/USDT"])

    def test_reconcile_sends_alert_for_ghost_position(self, engine, exchange, telegram, portfolio):
        """Position in DB for a symbol not in the trading list → alert."""
        portfolio.open_symbols.return_value = ["XYZ/USDT"]
        exchange.get_ticker.return_value = _make_ticker(50000.0)
        engine._reconcile_positions(["BTC/USDT"])
        telegram.send_risk_alert.assert_called_once()
        call_args = telegram.send_risk_alert.call_args[0][0]
        assert "XYZ/USDT" in call_args

    def test_reconcile_ticker_error_does_not_raise(self, engine, exchange, portfolio):
        """Exchange error during reconciliation is swallowed — engine still starts."""
        portfolio.open_symbols.return_value = []
        exchange.get_ticker.side_effect = RuntimeError("exchange down")
        engine._reconcile_positions(["BTC/USDT"])  # must not raise

    def test_reconcile_called_only_in_live_mode(
        self, settings, exchange, strategy, risk_manager, portfolio, telegram
    ):
        """Paper mode must NOT call reconciliation."""
        eng = TradingEngine(
            _make_settings("paper"), exchange, strategy, risk_manager, portfolio, telegram
        )
        mock_scheduler = MagicMock()
        mock_scheduler.running = False
        eng._scheduler = mock_scheduler

        with patch.object(eng, "_reconcile_positions") as mock_rec, \
             patch.object(eng, "_register_signal_handlers"):
            threading.Thread(target=eng.stop, daemon=True).start()
            eng.start(["BTC/USDT"], interval_seconds=60, daily_report_hour=None)

        mock_rec.assert_not_called()

    def test_reconcile_called_in_live_mode(
        self, settings, exchange, strategy, risk_manager, portfolio, telegram
    ):
        eng = TradingEngine(
            _make_settings("live"), exchange, strategy, risk_manager, portfolio, telegram
        )
        mock_scheduler = MagicMock()
        mock_scheduler.running = False
        eng._scheduler = mock_scheduler

        with patch.object(eng, "_reconcile_positions") as mock_rec, \
             patch.object(eng, "_register_signal_handlers"):
            threading.Thread(target=eng.stop, daemon=True).start()
            eng.start(["BTC/USDT"], interval_seconds=60, daily_report_hour=None)

        mock_rec.assert_called_once_with(["BTC/USDT"])
