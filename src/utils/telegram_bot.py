"""
Telegram Bot incoming message handler (long-polling).

Runs a background thread that polls the Telegram getUpdates API.
Dispatches slash commands to the trading engine.

Supported commands:
  /help      — List available commands
  /status    — Engine mode, paused state, circuit breaker
  /positions — Open positions
  /pnl       — Portfolio PnL summary
  /pause     — Pause trading (ticks skipped)
  /resume    — Resume trading

Usage:
    bot = TelegramBotController(telegram, engine, portfolio)
    bot.start()
    # engine runs...
    bot.stop()
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Callable, Protocol

from loguru import logger

from src.utils.telegram import TelegramClient


class EngineController(Protocol):
    """Structural protocol for TradingEngine — avoids circular imports."""

    @property
    def is_paused(self) -> bool: ...
    @property
    def mode(self) -> str: ...
    @property
    def circuit_breaker(self) -> object: ...

    def pause(self) -> None: ...
    def resume(self) -> None: ...


class TelegramBotController:
    """
    Long-polling Telegram bot that dispatches commands to the trading engine.

    The polling thread is a daemon thread — it exits automatically when the
    main process ends. Call stop() for a clean shutdown.

    Args:
        telegram:       Configured TelegramClient (must be enabled).
        engine:         Object conforming to EngineController protocol.
        portfolio_fn:   Zero-argument callable returning the portfolio PnL dict.
    """

    _POLL_TIMEOUT = 30  # Telegram long-poll seconds

    def __init__(
        self,
        telegram: TelegramClient,
        engine: EngineController,
        portfolio_fn: Callable[[], dict],
    ) -> None:
        self._telegram = telegram
        self._engine = engine
        self._portfolio_fn = portfolio_fn
        self._offset = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the polling thread. No-op if Telegram is not configured."""
        if not self._telegram.is_enabled:
            logger.debug("TelegramBotController: disabled (no token/chat_id)")
            return
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="telegram-bot-poll",
        )
        self._thread.start()
        logger.info("TelegramBotController started")

    def stop(self) -> None:
        """Signal the polling loop to exit."""
        self._stop_event.set()

    # ── Polling loop ──────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                updates = self._get_updates()
                for update in updates:
                    self._offset = update["update_id"] + 1
                    self._handle_update(update)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Telegram bot poll error", error=str(exc))

    def _get_updates(self) -> list[dict]:
        """Call getUpdates with long-polling. Returns empty list on error."""
        token = self._telegram._token  # noqa: SLF001
        url = (
            f"https://api.telegram.org/bot{token}/getUpdates"
            f"?offset={self._offset}&timeout={self._POLL_TIMEOUT}"
        )
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self._POLL_TIMEOUT + 5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("result", [])
        except Exception as exc:  # noqa: BLE001
            logger.debug("getUpdates failed", error=str(exc))
            return []

    def _handle_update(self, update: dict) -> None:
        """Extract command text and dispatch to the engine."""
        message = update.get("message") or update.get("edited_message", {})
        if not message:
            return
        text = message.get("text", "").strip()
        if not text.startswith("/"):
            return

        # Strip @BotName suffix (e.g. /status@MyTradingBot → /status)
        cmd = text.split()[0].split("@")[0].lower()
        logger.info("Telegram command received", command=cmd)

        reply = self._dispatch_command(cmd)
        self._telegram.send(reply)

    # ── Command dispatch ──────────────────────────────────────────────────────

    def _dispatch_command(self, cmd: str) -> str:
        dispatch = {
            "/help": self._fmt_help,
            "/status": self._fmt_status,
            "/positions": self._fmt_positions,
            "/pnl": self._fmt_pnl,
            "/pause": self._cmd_pause,
            "/resume": self._cmd_resume,
        }
        handler = dispatch.get(cmd)
        if handler is None:
            return f"❓ Unknown command: <code>{cmd}</code>\nSend /help for available commands."
        return handler()

    def _fmt_help(self) -> str:
        return (
            "🤖 <b>Trading Agent Commands</b>\n\n"
            "/status — Engine status\n"
            "/positions — Open positions\n"
            "/pnl — Portfolio PnL summary\n"
            "/pause — Pause trading\n"
            "/resume — Resume trading\n"
            "/help — Show this message"
        )

    def _fmt_status(self) -> str:
        paused = self._engine.is_paused
        mode = self._engine.mode
        cb = self._engine.circuit_breaker
        cb_state = getattr(cb, "state", None)
        cb_name = cb_state.name if cb_state is not None else "UNKNOWN"
        status_emoji = "⏸️" if paused else "▶️"
        circuit_emoji = "🟢" if cb_name == "CLOSED" else "🔴"
        return (
            f"{status_emoji} <b>Engine Status</b>\n"
            f"Mode: <code>{mode.upper()}</code>\n"
            f"State: <b>{'PAUSED' if paused else 'RUNNING'}</b>\n"
            f"{circuit_emoji} Circuit: <code>{cb_name}</code>"
        )

    def _fmt_positions(self) -> str:
        try:
            report = self._portfolio_fn()
            n = report.get("open_positions", 0)
            if n == 0:
                return "📊 <b>Positions</b>\nNo open positions."
            lines = [f"📊 <b>Open Positions ({n})</b>"]
            for sym, pos in report.get("positions", {}).items():
                pnl = pos.get("unrealized_pnl", 0.0)
                sign = "+" if pnl >= 0 else ""
                lines.append(
                    f"• <code>{sym}</code> — entry: {pos.get('entry_price', 0):,.2f} "
                    f"| uPnL: <b>{sign}{pnl:,.2f}</b>"
                )
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            return f"❌ Failed to get positions: {exc}"

    def _fmt_pnl(self) -> str:
        try:
            report = self._portfolio_fn()
            cash = float(report.get("cash", 0))
            realized = float(report.get("realized_pnl", 0))
            unrealized = float(report.get("unrealized_pnl", 0))
            sign_r = "+" if realized >= 0 else ""
            sign_u = "+" if unrealized >= 0 else ""
            emoji = "📈" if realized >= 0 else "📉"
            return (
                f"{emoji} <b>PnL Summary</b>\n"
                f"Cash: <code>{cash:,.2f}</code>\n"
                f"Realized: <b>{sign_r}{realized:,.2f}</b>\n"
                f"Unrealized: <b>{sign_u}{unrealized:,.2f}</b>"
            )
        except Exception as exc:  # noqa: BLE001
            return f"❌ Failed to get PnL: {exc}"

    def _cmd_pause(self) -> str:
        if self._engine.is_paused:
            return "⏸️ Engine is already paused."
        self._engine.pause()
        return "⏸️ <b>Trading paused.</b>\nSend /resume to restart."

    def _cmd_resume(self) -> str:
        if not self._engine.is_paused:
            return "▶️ Engine is already running."
        self._engine.resume()
        return "▶️ <b>Trading resumed.</b>"
