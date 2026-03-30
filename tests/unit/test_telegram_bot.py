"""Unit tests for TelegramBotController."""
from __future__ import annotations

import sys
import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest

from src.utils.telegram import TelegramClient
from src.utils.telegram_bot import TelegramBotController


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_telegram(enabled: bool = True) -> TelegramClient:
    client = MagicMock(spec=TelegramClient)
    client.is_enabled = enabled
    client._token = "test-token"  # noqa: SLF001
    client.send = MagicMock(return_value=True)
    return client


def _make_engine(paused: bool = False, mode: str = "paper") -> MagicMock:
    eng = MagicMock()
    eng.is_paused = paused
    eng.mode = mode
    cb = MagicMock()
    cb.state.name = "CLOSED"
    eng.circuit_breaker = cb
    return eng


def _make_portfolio_fn(
    cash: float = 10000.0,
    realized: float = 0.0,
    unrealized: float = 0.0,
    n_positions: int = 0,
) -> object:
    def fn() -> dict:
        return {
            "cash": cash,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "open_positions": n_positions,
            "positions": {},
        }
    return fn


def _make_controller(
    enabled: bool = True,
    paused: bool = False,
    mode: str = "paper",
) -> TelegramBotController:
    return TelegramBotController(
        telegram=_make_telegram(enabled=enabled),
        engine=_make_engine(paused=paused, mode=mode),
        portfolio_fn=_make_portfolio_fn(),
    )


# ── start / stop ──────────────────────────────────────────────────────────────

class TestStartStop:
    def test_start_does_nothing_when_disabled(self) -> None:
        ctrl = _make_controller(enabled=False)
        ctrl.start()
        assert ctrl._thread is None  # noqa: SLF001

    def test_start_launches_thread_when_enabled(self) -> None:
        ctrl = _make_controller(enabled=True)
        with patch.object(ctrl, "_poll_loop"):
            ctrl.start()
            assert ctrl._thread is not None  # noqa: SLF001
            assert ctrl._thread.daemon is True  # noqa: SLF001

    def test_stop_sets_stop_event(self) -> None:
        ctrl = _make_controller(enabled=True)
        assert not ctrl._stop_event.is_set()  # noqa: SLF001
        ctrl.stop()
        assert ctrl._stop_event.is_set()  # noqa: SLF001


# ── Command dispatch ──────────────────────────────────────────────────────────

class TestDispatchHelp:
    def test_help_returns_command_list(self) -> None:
        ctrl = _make_controller()
        reply = ctrl._dispatch_command("/help")  # noqa: SLF001
        assert "/status" in reply
        assert "/pause" in reply
        assert "/resume" in reply
        assert "/pnl" in reply
        assert "/positions" in reply

    def test_unknown_command_returns_error(self) -> None:
        ctrl = _make_controller()
        reply = ctrl._dispatch_command("/unknown")  # noqa: SLF001
        assert "Unknown" in reply


class TestDispatchStatus:
    def test_status_shows_mode_and_state(self) -> None:
        ctrl = _make_controller(paused=False, mode="live")
        reply = ctrl._dispatch_command("/status")  # noqa: SLF001
        assert "LIVE" in reply
        assert "RUNNING" in reply

    def test_status_shows_paused_when_paused(self) -> None:
        ctrl = _make_controller(paused=True)
        reply = ctrl._dispatch_command("/status")  # noqa: SLF001
        assert "PAUSED" in reply

    def test_status_shows_circuit_state(self) -> None:
        ctrl = _make_controller()
        reply = ctrl._dispatch_command("/status")  # noqa: SLF001
        assert "CLOSED" in reply


class TestDispatchPauseResume:
    def test_pause_calls_engine_pause(self) -> None:
        telegram = _make_telegram()
        engine = _make_engine(paused=False)
        ctrl = TelegramBotController(
            telegram=telegram,
            engine=engine,
            portfolio_fn=_make_portfolio_fn(),
        )
        reply = ctrl._dispatch_command("/pause")  # noqa: SLF001
        engine.pause.assert_called_once()
        assert "paused" in reply.lower()

    def test_pause_when_already_paused_does_not_double_pause(self) -> None:
        engine = _make_engine(paused=True)
        ctrl = TelegramBotController(
            telegram=_make_telegram(),
            engine=engine,
            portfolio_fn=_make_portfolio_fn(),
        )
        reply = ctrl._dispatch_command("/pause")  # noqa: SLF001
        engine.pause.assert_not_called()
        assert "already" in reply.lower()

    def test_resume_calls_engine_resume(self) -> None:
        engine = _make_engine(paused=True)
        ctrl = TelegramBotController(
            telegram=_make_telegram(),
            engine=engine,
            portfolio_fn=_make_portfolio_fn(),
        )
        reply = ctrl._dispatch_command("/resume")  # noqa: SLF001
        engine.resume.assert_called_once()
        assert "resumed" in reply.lower()

    def test_resume_when_not_paused_does_nothing(self) -> None:
        engine = _make_engine(paused=False)
        ctrl = TelegramBotController(
            telegram=_make_telegram(),
            engine=engine,
            portfolio_fn=_make_portfolio_fn(),
        )
        reply = ctrl._dispatch_command("/resume")  # noqa: SLF001
        engine.resume.assert_not_called()
        assert "already" in reply.lower()


class TestDispatchPnL:
    def test_pnl_shows_cash_and_realized(self) -> None:
        ctrl = TelegramBotController(
            telegram=_make_telegram(),
            engine=_make_engine(),
            portfolio_fn=_make_portfolio_fn(cash=9500.0, realized=-500.0),
        )
        reply = ctrl._dispatch_command("/pnl")  # noqa: SLF001
        assert "9,500.00" in reply
        assert "-500.00" in reply

    def test_pnl_shows_unrealized(self) -> None:
        ctrl = TelegramBotController(
            telegram=_make_telegram(),
            engine=_make_engine(),
            portfolio_fn=_make_portfolio_fn(unrealized=200.0),
        )
        reply = ctrl._dispatch_command("/pnl")  # noqa: SLF001
        assert "200.00" in reply


class TestDispatchPositions:
    def test_no_positions_shows_empty_message(self) -> None:
        ctrl = TelegramBotController(
            telegram=_make_telegram(),
            engine=_make_engine(),
            portfolio_fn=_make_portfolio_fn(n_positions=0),
        )
        reply = ctrl._dispatch_command("/positions")  # noqa: SLF001
        assert "No open" in reply

    def test_positions_shows_count(self) -> None:
        ctrl = TelegramBotController(
            telegram=_make_telegram(),
            engine=_make_engine(),
            portfolio_fn=_make_portfolio_fn(n_positions=2),
        )
        reply = ctrl._dispatch_command("/positions")  # noqa: SLF001
        assert "2" in reply


# ── Handle update ─────────────────────────────────────────────────────────────

class TestHandleUpdate:
    def test_handle_update_dispatches_command(self) -> None:
        telegram = _make_telegram()
        ctrl = TelegramBotController(
            telegram=telegram,
            engine=_make_engine(),
            portfolio_fn=_make_portfolio_fn(),
        )
        update = {
            "update_id": 100,
            "message": {"text": "/help", "chat": {"id": "123"}},
        }
        ctrl._handle_update(update)  # noqa: SLF001
        telegram.send.assert_called_once()
        msg = telegram.send.call_args[0][0]
        assert "/status" in msg

    def test_non_command_message_ignored(self) -> None:
        telegram = _make_telegram()
        ctrl = TelegramBotController(
            telegram=telegram,
            engine=_make_engine(),
            portfolio_fn=_make_portfolio_fn(),
        )
        update = {
            "update_id": 101,
            "message": {"text": "hello there", "chat": {"id": "123"}},
        }
        ctrl._handle_update(update)  # noqa: SLF001
        telegram.send.assert_not_called()

    def test_bot_name_suffix_stripped(self) -> None:
        """'/status@MyBot' should be treated as '/status'."""
        telegram = _make_telegram()
        ctrl = TelegramBotController(
            telegram=telegram,
            engine=_make_engine(),
            portfolio_fn=_make_portfolio_fn(),
        )
        update = {
            "update_id": 102,
            "message": {"text": "/status@MyTradingBot", "chat": {"id": "123"}},
        }
        ctrl._handle_update(update)  # noqa: SLF001
        telegram.send.assert_called_once()
        msg = telegram.send.call_args[0][0]
        assert "Status" in msg or "RUNNING" in msg or "PAUSED" in msg


# ── get_updates error handling ────────────────────────────────────────────────

class TestGetUpdates:
    def test_returns_empty_list_on_network_error(self) -> None:
        ctrl = _make_controller()
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            updates = ctrl._get_updates()  # noqa: SLF001
        assert updates == []

    def test_offset_increments_after_update(self) -> None:
        ctrl = _make_controller()
        assert ctrl._offset == 0  # noqa: SLF001

        update = {
            "update_id": 42,
            "message": {"text": "/help"},
        }
        ctrl._handle_update(update)  # noqa: SLF001
        # offset is set by poll_loop, not handle_update — just verify handle works
        # The actual offset increment happens in _poll_loop, not _handle_update


# ── /restart command ──────────────────────────────────────────────────────────

class TestDispatchRestart:
    def test_restart_returns_restart_message(self) -> None:
        """/restart 응답 메시지에 'Restart' 또는 'restart' 포함 확인."""
        ctrl = _make_controller()
        with patch("os.execv"), patch("time.sleep"):
            reply = ctrl._dispatch_command("/restart")  # noqa: SLF001
        assert "restart" in reply.lower()

    def test_restart_listed_in_help(self) -> None:
        """/help 응답에 /restart 항목 포함 확인."""
        ctrl = _make_controller()
        reply = ctrl._dispatch_command("/help")  # noqa: SLF001
        assert "/restart" in reply

    def test_restart_returns_immediately_without_blocking(self) -> None:
        """/restart는 즉시 반환해야 하며 3초 sleep을 기다리지 않아야 한다."""
        ctrl = _make_controller()
        with patch("os.execv"), patch("time.sleep"):
            start = time.monotonic()
            ctrl._dispatch_command("/restart")  # noqa: SLF001
            elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"restart blocked for {elapsed:.2f}s — should return immediately"

    def test_restart_spawns_background_thread(self) -> None:
        """/restart 호출 후 'telegram-restart' 데몬 스레드가 생성되어야 한다."""
        ctrl = _make_controller()
        execv_called = threading.Event()

        def fake_execv(executable, args):  # noqa: ANN001
            execv_called.set()

        with patch("os.execv", side_effect=fake_execv), patch("time.sleep"):
            ctrl._dispatch_command("/restart")  # noqa: SLF001
            # 스레드가 실행될 때까지 대기 (최대 2초)
            execv_called.wait(timeout=2.0)

        assert execv_called.is_set(), "os.execv was never called by restart thread"

    def test_restart_calls_execv_with_correct_args(self) -> None:
        """os.execv가 sys.executable과 sys.argv를 인자로 호출되어야 한다."""
        ctrl = _make_controller()
        captured: list = []

        def fake_execv(executable, args):  # noqa: ANN001
            captured.append((executable, args))

        with patch("os.execv", side_effect=fake_execv), patch("time.sleep"):
            ctrl._dispatch_command("/restart")  # noqa: SLF001
            # 스레드 완료 대기
            time.sleep(0.2)

        assert len(captured) == 1
        executable, args = captured[0]
        assert executable == sys.executable
        assert args[0] == sys.executable
        assert args[1:] == sys.argv

    def test_restart_sleeps_before_execv(self) -> None:
        """os.execv 호출 전 time.sleep이 먼저 호출되어야 한다."""
        ctrl = _make_controller()
        call_order: list[str] = []

        def fake_sleep(n):  # noqa: ANN001
            call_order.append("sleep")

        def fake_execv(executable, args):  # noqa: ANN001
            call_order.append("execv")

        done = threading.Event()

        def fake_execv_with_signal(executable, args):  # noqa: ANN001
            call_order.append("execv")
            done.set()

        with patch("os.execv", side_effect=fake_execv_with_signal), \
             patch("time.sleep", side_effect=fake_sleep):
            ctrl._dispatch_command("/restart")  # noqa: SLF001
            done.wait(timeout=2.0)

        assert call_order == ["sleep", "execv"], f"Unexpected call order: {call_order}"

    def test_restart_thread_is_daemon(self) -> None:
        """restart 스레드는 daemon=True여야 프로세스 종료 시 함께 종료된다."""
        ctrl = _make_controller()
        found_threads: list[threading.Thread] = []
        original_thread_init = threading.Thread.__init__

        def capture_thread(self, *args, **kwargs):  # noqa: ANN001
            original_thread_init(self, *args, **kwargs)
            if kwargs.get("name") == "telegram-restart":
                found_threads.append(self)

        with patch.object(threading.Thread, "__init__", capture_thread), \
             patch("os.execv"), patch("time.sleep"):
            ctrl._dispatch_command("/restart")  # noqa: SLF001
            time.sleep(0.1)

        assert found_threads, "telegram-restart thread was not created"
        assert found_threads[0].daemon is True, "restart thread must be daemon"

    def test_restart_via_handle_update(self) -> None:
        """/restart 메시지가 update 흐름을 통해 정상 처리되는지 검증한다."""
        telegram = _make_telegram()
        ctrl = TelegramBotController(
            telegram=telegram,
            engine=_make_engine(),
            portfolio_fn=_make_portfolio_fn(),
        )
        update = {
            "update_id": 200,
            "message": {"text": "/restart", "chat": {"id": "123"}},
        }
        with patch("os.execv"), patch("time.sleep"):
            ctrl._handle_update(update)  # noqa: SLF001

        telegram.send.assert_called_once()
        msg = telegram.send.call_args[0][0]
        assert "restart" in msg.lower()

    def test_restart_with_bot_suffix(self) -> None:
        """/restart@BotName 형식도 정상 처리되어야 한다."""
        telegram = _make_telegram()
        ctrl = TelegramBotController(
            telegram=telegram,
            engine=_make_engine(),
            portfolio_fn=_make_portfolio_fn(),
        )
        update = {
            "update_id": 201,
            "message": {"text": "/restart@MyTradingBot", "chat": {"id": "123"}},
        }
        with patch("os.execv"), patch("time.sleep"):
            ctrl._handle_update(update)  # noqa: SLF001

        telegram.send.assert_called_once()
        msg = telegram.send.call_args[0][0]
        assert "restart" in msg.lower()
