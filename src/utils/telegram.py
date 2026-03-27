"""
Telegram notification client.
Sends messages via Bot API using urllib (no extra dependencies).
Configured via TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal
from typing import Any

from loguru import logger


class TelegramClient:
    """
    Thin Telegram Bot API wrapper.

    Usage:
        client = TelegramClient(bot_token="...", chat_id="...")
        client.send("Hello!")
        client.send_order_filled("BTC/USDT", "buy", 0.01, 50000.0)
    """

    _BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)
        if not self._enabled:
            logger.debug("TelegramClient: token or chat_id missing, notifications disabled")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Send a plain text message.

        Returns:
            True if sent successfully, False otherwise (never raises).
        """
        if not self._enabled:
            return False

        url = self._BASE_URL.format(token=self._token)
        payload = json.dumps({
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }).encode("utf-8")

        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                success = resp.status == 200
                if not success:
                    logger.warning("Telegram API returned non-200", status=resp.status)
                return success
        except Exception as exc:
            logger.warning("Telegram send failed", error=str(exc))
            return False

    # ── Formatted event messages ──────────────────────────────────────────────

    def send_order_filled(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        order_id: str = "",
    ) -> bool:
        emoji = "🟢" if side == "buy" else "🔴"
        text = (
            f"{emoji} <b>Order Filled</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Side: <b>{side.upper()}</b>\n"
            f"Amount: {amount:.8f}\n"
            f"Price: {price:,.2f}\n"
        )
        if order_id:
            text += f"Order ID: <code>{order_id}</code>"
        return self.send(text)

    def send_position_closed(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
    ) -> bool:
        emoji = "✅" if pnl >= 0 else "❌"
        sign = "+" if pnl >= 0 else ""
        text = (
            f"{emoji} <b>Position Closed</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Entry: {entry_price:,.2f} → Exit: {exit_price:,.2f}\n"
            f"PnL: <b>{sign}{pnl:,.2f} ({sign}{pnl_pct:.2f}%)</b>"
        )
        return self.send(text)

    def send_risk_alert(self, message: str) -> bool:
        text = f"⚠️ <b>RISK ALERT</b>\n{message}"
        return self.send(text)

    def send_error(self, error: str, context: str = "") -> bool:
        text = f"🚨 <b>ERROR</b>\n"
        if context:
            text += f"Context: {context}\n"
        text += f"<code>{error}</code>"
        return self.send(text)

    def send_daily_report(
        self,
        date: datetime,
        initial_capital: float,
        final_capital: float,
        realized_pnl: float,
        total_trades: int,
        win_rate: float,
    ) -> bool:
        pnl_pct = (realized_pnl / initial_capital * 100) if initial_capital else 0.0
        sign = "+" if realized_pnl >= 0 else ""
        emoji = "📈" if realized_pnl >= 0 else "📉"
        text = (
            f"{emoji} <b>Daily Report — {date.strftime('%Y-%m-%d')}</b>\n"
            f"Capital: {initial_capital:,.2f} → {final_capital:,.2f}\n"
            f"PnL: <b>{sign}{realized_pnl:,.2f} ({sign}{pnl_pct:.2f}%)</b>\n"
            f"Trades: {total_trades}  |  Win Rate: {win_rate:.1f}%"
        )
        return self.send(text)


    def send_startup(
        self,
        mode: str,
        exchange: str,
        symbols: list[str],
        strategy: str,
    ) -> bool:
        text = (
            f"🚀 <b>Trading Agent Started</b>\n"
            f"Mode: <b>{mode.upper()}</b>  |  Exchange: <code>{exchange}</code>\n"
            f"Symbols: {', '.join(f'<code>{s}</code>' for s in symbols)}\n"
            f"Strategy: <code>{strategy}</code>"
        )
        return self.send(text)

    def send_shutdown(
        self,
        session_seconds: float,
        total_trades: int,
        win_rate: float,
        realized_pnl: float,
    ) -> bool:
        hours, rem = divmod(int(session_seconds), 3600)
        minutes = rem // 60
        sign = "+" if realized_pnl >= 0 else ""
        text = (
            f"🛑 <b>Trading Agent Stopped</b>\n"
            f"Session: {hours}h {minutes}m\n"
            f"Trades: {total_trades}  |  Win Rate: {win_rate:.1f}%\n"
            f"Realized PnL: <b>{sign}{realized_pnl:,.2f}</b>"
        )
        return self.send(text)

    def send_heartbeat(
        self,
        open_positions: int,
        cash: float,
        circuit_state: str,
    ) -> bool:
        state_emoji = "🟢" if circuit_state == "CLOSED" else "🔴"
        text = (
            f"{state_emoji} <b>Heartbeat</b>\n"
            f"Positions: {open_positions}  |  Cash: {cash:,.2f}\n"
            f"Circuit: <code>{circuit_state}</code>"
        )
        return self.send(text)


def get_telegram_client() -> TelegramClient:
    """Build TelegramClient from application settings."""
    from src.config.settings import get_settings
    settings = get_settings()
    return TelegramClient(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )
