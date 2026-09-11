"""
Telegram notification client.
Sends messages via Bot API using urllib (no extra dependencies).
Configured via TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from loguru import logger

from src.utils.quiet_hours import QuietHours


class TelegramClient:
    """
    Thin Telegram Bot API wrapper.

    hourly_summary=True: suppresses all real-time messages; only
    flush_summary() sends, once per hour via the engine heartbeat.
    """

    _BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        hourly_summary: bool = False,
        notify_level: Literal["all", "critical"] = "all",
        quiet_hours: QuietHours | None = None,
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)
        self._hourly_summary = hourly_summary
        self._notify_level = notify_level
        self._quiet_hours = quiet_hours
        self._buffer: list[str] = []
        self._buffer_lock = threading.Lock()
        if not self._enabled:
            logger.debug("TelegramClient: token or chat_id missing, notifications disabled")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ── Notification level / quiet hours ──────────────────────────────────────

    def _suppress_info(self) -> bool:
        """
        True if info-tier notifications should be suppressed right now.
        Critical-tier alerts (send_risk_alert / send_error) always bypass this.
        """
        if getattr(self, "_notify_level", "all") == "critical":
            return True
        quiet_hours = getattr(self, "_quiet_hours", None)
        if quiet_hours is not None and quiet_hours.is_quiet():
            return True
        return False

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a plain text message. Never raises."""
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

    # ── Internal buffer ───────────────────────────────────────────────────────

    def _queue(self, text: str) -> None:
        with self._buffer_lock:
            self._buffer.append(text)

    # ── Hourly summary (the ONLY outgoing message in hourly_summary mode) ─────

    def flush_summary(
        self,
        mode: str = "live",
        paused: bool = False,
        circuit_state: str = "CLOSED",
        open_positions: int = 0,
        cash: float = 0.0,
        realized_pnl: float = 0.0,
        total_trades: int = 0,
        wins: int = 0,
        losses: int = 0,
        win_rate_pct: float = 0.0,
        profit_factor: float = 0.0,
        avg_win: float = 0.0,
        avg_loss: float = 0.0,
        positions_detail: list[dict] | None = None,
    ) -> bool:
        """Compose and send the hourly summary, then clear the event buffer.

        If suppressed (notify_level='critical' or active quiet hours), the
        buffer is left untouched so events carry over to the next flush —
        suppression must never silently drop events.

        positions_detail: list of dicts with keys:
            symbol, entry_price, current_price, amount, pnl, pnl_pct, stop_loss, take_profit
        """
        if self._suppress_info():
            return False

        with self._buffer_lock:
            events = list(self._buffer)
            self._buffer.clear()

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        circuit_emoji = "🟢" if circuit_state == "CLOSED" else "🔴"
        state_label = "⏸ 일시정지" if paused else "▶ 실행 중"
        pnl_sign = "+" if realized_pnl >= 0 else ""
        pnl_emoji = "📈" if realized_pnl >= 0 else "📉"
        pf_display = f"{profit_factor:.2f}" if profit_factor < 999 else "∞"

        lines = [
            f"⚡ <b>Trading Agent 시간별 요약</b>  <code>{now}</code>",
            "",
            "<b>── 엔진 상태</b>",
            f"  모드: <code>{mode.upper()}</code>  |  상태: {state_label}",
            f"  서킷 브레이커: {circuit_emoji} <code>{circuit_state}</code>",
            f"  오픈 포지션: <b>{open_positions}</b>건  |  현금: <b>₩{cash:,.0f}</b>",
            "",
            f"<b>── 성과 분석</b>  {pnl_emoji}",
            f"  실현 손익: <b>{pnl_sign}₩{realized_pnl:,.0f}</b>",
            f"  총 거래: {total_trades}건  (승 {wins} / 패 {losses})",
            f"  승률: <b>{win_rate_pct:.1f}%</b>  |  수익 팩터: <b>{pf_display}</b>",
            f"  평균 수익: +₩{avg_win:,.0f}  |  평균 손실: -₩{avg_loss:,.0f}",
        ]

        # ── Open positions detail ─────────────────────────────────────────────
        if positions_detail:
            lines += ["", "<b>── 보유 포지션</b>"]
            for pos in positions_detail:
                sym = pos.get("symbol", "?")
                entry = pos.get("entry_price", 0.0)
                current = pos.get("current_price", 0.0)
                pnl_pos = pos.get("pnl", 0.0)
                pnl_pct_pos = pos.get("pnl_pct", 0.0)
                sl = pos.get("stop_loss", 0.0)
                tp = pos.get("take_profit", 0.0)
                sign = "+" if pnl_pos >= 0 else ""
                emoji = "📈" if pnl_pos >= 0 else "📉"
                lines.append(
                    f"  {emoji} <code>{sym}</code>  진입 {entry:,.1f} → 현재 {current:,.1f}"
                    f"  <b>{sign}₩{pnl_pos:,.0f} ({sign}{pnl_pct_pos:.1f}%)</b>"
                    f"\n       SL {sl:,.1f}  /  TP {tp:,.1f}"
                )

        # ── Events ────────────────────────────────────────────────────────────
        if events:
            lines += ["", f"<b>── 이번 시간 이벤트</b> ({len(events)}건)"]
            lines += [f"  {e}" for e in events]
        else:
            lines += ["", "  이번 시간 이벤트 없음"]

        return self.send("\n".join(lines))

    # ── Event methods — all silenced in hourly_summary mode ──────────────────

    def send_order_filled(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        order_id: str = "",
    ) -> bool:
        if self._suppress_info():
            return True  # suppressed by notify_level/quiet hours
        emoji = "🟢" if side == "buy" else "🔴"
        line = f"{emoji} {side.upper()} <code>{symbol}</code> {amount:.6f} @ ₩{price:,.0f}"
        if getattr(self, '_hourly_summary', False):
            self._queue(line)
            return True
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
        if self._suppress_info():
            return True  # suppressed by notify_level/quiet hours
        emoji = "✅" if pnl >= 0 else "❌"
        sign = "+" if pnl >= 0 else ""
        line = f"{emoji} CLOSE <code>{symbol}</code> {sign}₩{pnl:,.0f} ({sign}{pnl_pct:.2f}%)"
        if getattr(self, '_hourly_summary', False):
            self._queue(line)
            return True
        text = (
            f"{emoji} <b>Position Closed</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Entry: {entry_price:,.2f} → Exit: {exit_price:,.2f}\n"
            f"PnL: <b>{sign}{pnl:,.2f} ({sign}{pnl_pct:.2f}%)</b>"
        )
        return self.send(text)

    def send_risk_alert(self, message: str) -> bool:
        """Critical tier — always sent immediately, regardless of
        hourly_summary/notify_level/quiet hours. Also queued (when
        hourly_summary is on) so the hourly digest's event list stays complete."""
        if getattr(self, '_hourly_summary', False):
            self._queue(f"⚠️ {message}")
        return self.send(f"⚠️ <b>RISK ALERT</b>\n{message}")

    def send_error(self, error: str, context: str = "") -> bool:
        """Critical tier — see send_risk_alert docstring."""
        if getattr(self, '_hourly_summary', False):
            ctx = f"[{context}] " if context else ""
            self._queue(f"🚨 {ctx}{error[:80]}")
        text = f"🚨 <b>ERROR</b>\n"
        if context:
            text += f"Context: {context}\n"
        text += f"<code>{error}</code>"
        return self.send(text)

    def send_startup(self, mode: str, exchange: str, symbols: list[str], strategy: str) -> bool:
        if self._suppress_info():
            return True  # suppressed by notify_level/quiet hours
        if getattr(self, '_hourly_summary', False):
            return True  # suppress
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
        if self._suppress_info():
            return True  # suppressed by notify_level/quiet hours
        if getattr(self, '_hourly_summary', False):
            return True  # suppress
        hours, rem = divmod(int(session_seconds), 3600)
        minutes = rem // 60
        sign = "+" if realized_pnl >= 0 else ""
        return self.send(
            f"🛑 <b>Trading Agent Stopped</b>\n"
            f"Session: {hours}h {minutes}m\n"
            f"Trades: {total_trades}  |  Win Rate: {win_rate:.1f}%\n"
            f"Realized PnL: <b>{sign}{realized_pnl:,.2f}</b>"
        )

    def send_daily_report(
        self,
        date: datetime,
        initial_capital: float,
        final_capital: float,
        realized_pnl: float,
        total_trades: int,
        win_rate: float,
    ) -> bool:
        if self._suppress_info():
            return True  # suppressed by notify_level/quiet hours
        if getattr(self, '_hourly_summary', False):
            return True  # suppress — covered by hourly summary
        pnl_pct = (realized_pnl / initial_capital * 100) if initial_capital else 0.0
        sign = "+" if realized_pnl >= 0 else ""
        emoji = "📈" if realized_pnl >= 0 else "📉"
        return self.send(
            f"{emoji} <b>Daily Report — {date.strftime('%Y-%m-%d')}</b>\n"
            f"Capital: {initial_capital:,.2f} → {final_capital:,.2f}\n"
            f"PnL: <b>{sign}{realized_pnl:,.2f} ({sign}{pnl_pct:.2f}%)</b>\n"
            f"Trades: {total_trades}  |  Win Rate: {win_rate:.1f}%"
        )

    def send_heartbeat(self, open_positions: int, cash: float, circuit_state: str) -> bool:
        if self._suppress_info():
            return True  # suppressed by notify_level/quiet hours
        if getattr(self, '_hourly_summary', False):
            return True  # suppress — replaced by flush_summary
        state_emoji = "🟢" if circuit_state == "CLOSED" else "🔴"
        return self.send(
            f"{state_emoji} <b>Heartbeat</b>\n"
            f"Positions: {open_positions}  |  Cash: {cash:,.2f}\n"
            f"Circuit: <code>{circuit_state}</code>"
        )


def get_telegram_client() -> TelegramClient:
    """Build TelegramClient from application settings."""
    from src.config.settings import get_settings
    from src.utils.quiet_hours import quiet_hours_from_settings
    settings = get_settings()
    return TelegramClient(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        hourly_summary=True,
        notify_level=settings.telegram_notify_level,
        quiet_hours=quiet_hours_from_settings(settings),
    )
