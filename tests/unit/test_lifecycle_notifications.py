"""Unit tests for engine lifecycle notifications and heartbeat."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_engine(telegram=None):
    from src.engine import TradingEngine
    from src.portfolio.tracker import PortfolioTracker
    from src.risk.manager import PortfolioState, RiskManager

    settings = MagicMock()
    settings.trading_mode = "paper"
    settings.exchange = "binance"
    settings.max_position_risk = 0.02
    settings.max_open_positions = 5
    settings.max_drawdown_halt = 0.15
    settings.max_daily_loss = 0.05

    portfolio = PortfolioTracker(initial_cash=Decimal("10000"))
    risk_state = PortfolioState(capital=Decimal("10000"), peak_capital=Decimal("10000"))
    risk_manager = RiskManager(settings, risk_state)

    tg = telegram if telegram is not None else MagicMock()
    if isinstance(tg, MagicMock):
        tg._hourly_summary = False

    return TradingEngine(
        settings=settings,
        exchange=MagicMock(),
        strategy=MagicMock(),
        risk_manager=risk_manager,
        portfolio=portfolio,
        telegram=tg,
    ), tg


# ── TelegramClient new methods ────────────────────────────────────────────────

class TestTelegramNewMethods:
    def _make_client(self):
        from src.utils.telegram import TelegramClient
        client = TelegramClient.__new__(TelegramClient)
        client._token = "tok"
        client._chat_id = "123"
        client._enabled = True
        return client

    def test_send_startup_calls_send(self):
        client = self._make_client()
        with patch.object(client, "send", return_value=True) as mock_send:
            result = client.send_startup(
                mode="paper",
                exchange="binance",
                symbols=["BTC/USDT", "ETH/USDT"],
                strategy="MACrossoverStrategy",
            )
        assert result is True
        text = mock_send.call_args[0][0]
        assert "PAPER" in text
        assert "binance" in text
        assert "BTC/USDT" in text
        assert "MACrossoverStrategy" in text

    def test_send_shutdown_calls_send(self):
        client = self._make_client()
        with patch.object(client, "send", return_value=True) as mock_send:
            result = client.send_shutdown(
                session_seconds=3720.0,
                total_trades=10,
                win_rate=60.0,
                realized_pnl=250.0,
            )
        assert result is True
        text = mock_send.call_args[0][0]
        assert "1h 2m" in text
        assert "10" in text
        assert "60.0%" in text
        assert "+250" in text

    def test_send_shutdown_negative_pnl(self):
        client = self._make_client()
        with patch.object(client, "send", return_value=True) as mock_send:
            client.send_shutdown(
                session_seconds=60.0,
                total_trades=3,
                win_rate=33.3,
                realized_pnl=-100.0,
            )
        text = mock_send.call_args[0][0]
        assert "-100" in text

    def test_send_heartbeat_closed_circuit(self):
        client = self._make_client()
        with patch.object(client, "send", return_value=True) as mock_send:
            result = client.send_heartbeat(
                open_positions=2,
                cash=9500.0,
                circuit_state="CLOSED",
            )
        assert result is True
        text = mock_send.call_args[0][0]
        assert "🟢" in text
        assert "2" in text
        assert "CLOSED" in text

    def test_send_heartbeat_open_circuit(self):
        client = self._make_client()
        with patch.object(client, "send", return_value=True) as mock_send:
            client.send_heartbeat(
                open_positions=0,
                cash=10000.0,
                circuit_state="OPEN",
            )
        text = mock_send.call_args[0][0]
        assert "🔴" in text
        assert "OPEN" in text

    def test_disabled_client_returns_false(self):
        from src.utils.telegram import TelegramClient
        client = TelegramClient("", "")
        assert client.send_startup("paper", "binance", ["BTC/USDT"], "MA") is False
        assert client.send_shutdown(60.0, 0, 0.0, 0.0) is False
        assert client.send_heartbeat(0, 10000.0, "CLOSED") is False


# ── Engine sends startup notification ─────────────────────────────────────────

class TestEngineStartupNotification:
    def test_startup_telegram_called_on_start(self):
        engine, telegram = _make_engine()

        with patch.object(engine._scheduler, "start"), \
             patch.object(engine._scheduler, "add_job"), \
             patch.object(engine._scheduler, "shutdown"), \
             patch.object(engine._stop_event, "wait"):
            engine.start(symbols=["BTC/USDT"], interval_seconds=60, heartbeat_interval=None)

        telegram.send_startup.assert_called_once()
        call_kwargs = telegram.send_startup.call_args[1]
        assert call_kwargs["mode"] == "paper"
        assert call_kwargs["exchange"] == "binance"
        assert "BTC/USDT" in call_kwargs["symbols"]

    def test_startup_not_called_in_live_mode_reconcile_fail(self):
        """Even in live mode, startup notification fires (after reconcile)."""
        engine, telegram = _make_engine()
        engine._mode = "live"
        engine._settings.trading_mode = "live"

        with patch.object(engine, "_reconcile_positions"), \
             patch.object(engine._scheduler, "start"), \
             patch.object(engine._scheduler, "add_job"), \
             patch.object(engine._scheduler, "shutdown"), \
             patch.object(engine._stop_event, "wait"):
            engine.start(symbols=["BTC/USDT"], heartbeat_interval=None)

        telegram.send_startup.assert_called_once()


# ── Engine sends shutdown notification ───────────────────────────────────────

class TestEngineShutdownNotification:
    def test_shutdown_telegram_called(self):
        engine, telegram = _make_engine()
        engine._start_time = 0.0  # ensure session_seconds computable

        with patch("src.engine.time.monotonic", return_value=3600.0), \
             patch.object(engine._scheduler, "shutdown"):
            engine._shutdown()

        telegram.send_shutdown.assert_called_once()
        call_kwargs = telegram.send_shutdown.call_args[1]
        assert call_kwargs["total_trades"] == 0
        assert call_kwargs["win_rate"] == 0.0

    def test_shutdown_when_start_time_none(self):
        """If start_time is not set, session_seconds defaults to 0."""
        engine, telegram = _make_engine()
        engine._start_time = None

        with patch.object(engine._scheduler, "shutdown"):
            engine._shutdown()

        telegram.send_shutdown.assert_called_once()
        call_kwargs = telegram.send_shutdown.call_args[1]
        assert call_kwargs["session_seconds"] == 0.0


# ── Heartbeat ─────────────────────────────────────────────────────────────────

class TestHeartbeat:
    def test_heartbeat_sends_telegram(self):
        engine, telegram = _make_engine()
        engine._send_heartbeat()
        telegram.send_heartbeat.assert_called_once()
        call_kwargs = telegram.send_heartbeat.call_args[1]
        assert call_kwargs["open_positions"] == 0
        assert call_kwargs["circuit_state"] == "CLOSED"

    def test_heartbeat_circuit_state_reflected(self):
        engine, telegram = _make_engine()
        # Force circuit to OPEN
        for _ in range(5):
            try:
                with engine.circuit_breaker:
                    raise ValueError()
            except ValueError:
                pass

        engine._send_heartbeat()
        call_kwargs = telegram.send_heartbeat.call_args[1]
        assert call_kwargs["circuit_state"] == "OPEN"

    def test_heartbeat_logs_even_if_telegram_disabled(self):
        from src.utils.telegram import TelegramClient
        disabled_tg = TelegramClient("", "")
        engine, _ = _make_engine(telegram=disabled_tg)
        # Should not raise
        engine._send_heartbeat()

    def test_heartbeat_swallows_exceptions(self):
        engine, telegram = _make_engine()
        telegram.send_heartbeat.side_effect = RuntimeError("network error")
        # Must not raise
        engine._send_heartbeat()

    def test_heartbeat_scheduled_when_interval_provided(self):
        engine, telegram = _make_engine()
        jobs_added = []

        def fake_add_job(func, **kwargs):
            jobs_added.append(kwargs.get("id"))

        with patch.object(engine._scheduler, "add_job", side_effect=fake_add_job), \
             patch.object(engine._scheduler, "start"), \
             patch.object(engine._scheduler, "shutdown"), \
             patch.object(engine._stop_event, "wait"):
            engine.start(
                symbols=["BTC/USDT"],
                interval_seconds=60,
                daily_report_hour=None,
                heartbeat_interval=300,
            )

        assert "heartbeat" in jobs_added

    def test_heartbeat_not_scheduled_when_disabled(self):
        engine, _ = _make_engine()
        jobs_added = []

        def fake_add_job(func, **kwargs):
            jobs_added.append(kwargs.get("id"))

        with patch.object(engine._scheduler, "add_job", side_effect=fake_add_job), \
             patch.object(engine._scheduler, "start"), \
             patch.object(engine._scheduler, "shutdown"), \
             patch.object(engine._stop_event, "wait"):
            engine.start(
                symbols=["BTC/USDT"],
                interval_seconds=60,
                daily_report_hour=None,
                heartbeat_interval=None,
            )

        assert "heartbeat" not in jobs_added


# ── main.py integration ───────────────────────────────────────────────────────

class TestMainHeartbeatFlag:
    def test_default_heartbeat_interval_passed(self):
        import sys
        from contextlib import ExitStack
        from unittest.mock import patch, MagicMock

        mock_engine = MagicMock()
        settings = MagicMock()
        settings.trading_mode = "paper"
        settings.exchange = "binance"
        settings.binance_api_key = "k"
        settings.binance_secret_key = "s"
        settings.upbit_access_key = "k"
        settings.upbit_secret_key = "s"
        settings.max_position_risk = 0.02
        settings.max_open_positions = 5
        settings.max_daily_loss = 0.05
        settings.max_drawdown_halt = 0.15

        with ExitStack() as stack:
            stack.enter_context(patch("src.main.get_settings", return_value=settings))
            stack.enter_context(patch("src.main.setup_logger"))
            stack.enter_context(patch("src.exchange.binance.BinanceClient"))
            stack.enter_context(patch("src.exchange.upbit.UpbitClient"))
            stack.enter_context(patch("src.portfolio.tracker.PortfolioTracker"))
            stack.enter_context(patch("src.portfolio.position_store.SQLitePositionStore"))
            stack.enter_context(patch("src.risk.manager.PortfolioState"))
            stack.enter_context(patch("src.risk.manager.RiskManager"))
            stack.enter_context(patch("src.strategy.registry.get_strategy"))
            stack.enter_context(patch("src.utils.telegram.get_telegram_client"))
            stack.enter_context(patch("src.portfolio.journal_store.SQLiteJournalStore"))
            stack.enter_context(patch("src.engine.TradingEngine", return_value=mock_engine))

            old_argv = sys.argv
            sys.argv = ["trading_agent", "--skip-checks"]
            try:
                from src.main import main
                main()
            finally:
                sys.argv = old_argv

        call_kwargs = mock_engine.start.call_args[1]
        # Default heartbeat_interval=3600, but 0 maps to None
        assert "heartbeat_interval" in call_kwargs


# ── Consecutive-loss cooldown — immediate critical alert ─────────────────────

class TestCooldownAlert:
    def test_helper_sends_alert_when_triggered(self):
        engine, telegram = _make_engine()
        engine._notify_if_cooldown_triggered(True)
        telegram.send_risk_alert.assert_called_once()

    def test_helper_does_nothing_when_not_triggered(self):
        engine, telegram = _make_engine()
        engine._notify_if_cooldown_triggered(False)
        telegram.send_risk_alert.assert_not_called()

    def test_helper_alert_message_mentions_limit_and_cooldown(self):
        engine, telegram = _make_engine()
        engine._risk_manager.consecutive_loss_limit = 3
        engine._risk_manager.consecutive_loss_cooldown_minutes = 720
        engine._notify_if_cooldown_triggered(True)
        msg = telegram.send_risk_alert.call_args[0][0]
        assert "3" in msg
        assert "720" in msg

    def test_dust_removal_triggers_immediate_alert_on_cooldown(self):
        """End-to-end: a real on_position_closed() call site (dust removal in
        _check_exit_conditions) must wire its return value into the helper."""
        engine, telegram = _make_engine()
        engine._risk_manager.consecutive_loss_limit = 1
        engine._risk_manager.consecutive_loss_cooldown_minutes = 60
        engine._portfolio.open_position(
            "BTC/USDT", "buy", Decimal("0.0001"), Decimal("100"),
        )
        engine._exchange.get_ticker.return_value = MagicMock(last=Decimal("1"))

        engine._check_exit_conditions("BTC/USDT")

        telegram.send_risk_alert.assert_called_once()


# ── Exchange latency recording (feeds src/health.py) ──────────────────────────

class TestExchangeLatencyRecording:
    def test_tick_records_latency_on_success(self):
        engine, _telegram = _make_engine()
        with patch.object(engine, "_process_symbol"), \
             patch("src.engine.get_health_state") as mock_get_health:
            mock_state = MagicMock()
            mock_get_health.return_value = mock_state
            engine.tick("BTC/USDT")
        mock_state.record_latency_ms.assert_called_once()
        elapsed_ms = mock_state.record_latency_ms.call_args[0][0]
        assert elapsed_ms >= 0

    def test_tick_does_not_record_latency_on_failure(self):
        engine, _telegram = _make_engine()
        with patch.object(engine, "_process_symbol", side_effect=RuntimeError("boom")), \
             patch("src.engine.get_health_state") as mock_get_health:
            mock_state = MagicMock()
            mock_get_health.return_value = mock_state
            engine.tick("BTC/USDT")
        mock_state.record_latency_ms.assert_not_called()


# ── Balance anomaly detection (30-min heartbeat cadence) ──────────────────────

class TestBalanceAnomalyAlert:
    def test_heartbeat_records_equity_snapshot(self):
        engine, telegram = _make_engine()
        telegram._hourly_summary = True
        with patch("src.engine.get_health_state") as mock_get_health:
            mock_state = MagicMock()
            mock_state.record_equity_snapshot.return_value = None
            mock_get_health.return_value = mock_state
            engine._send_heartbeat()
        mock_state.record_equity_snapshot.assert_called_once()
        equity_arg = mock_state.record_equity_snapshot.call_args[0][0]
        assert equity_arg == pytest.approx(10000.0)  # initial cash, no open positions

    def test_heartbeat_sends_critical_alert_on_anomaly(self):
        engine, telegram = _make_engine()
        telegram._hourly_summary = True
        with patch("src.engine.get_health_state") as mock_get_health:
            mock_state = MagicMock()
            mock_state.record_equity_snapshot.return_value = {
                "previous": 10000.0, "current": 6000.0, "drop_pct": 40.0,
            }
            mock_get_health.return_value = mock_state
            engine._send_heartbeat()
        telegram.send_risk_alert.assert_called_once()
        msg = telegram.send_risk_alert.call_args[0][0]
        assert "40" in msg

    def test_heartbeat_no_alert_when_no_anomaly(self):
        engine, telegram = _make_engine()
        telegram._hourly_summary = True
        with patch("src.engine.get_health_state") as mock_get_health:
            mock_state = MagicMock()
            mock_state.record_equity_snapshot.return_value = None
            mock_get_health.return_value = mock_state
            engine._send_heartbeat()
        telegram.send_risk_alert.assert_not_called()
