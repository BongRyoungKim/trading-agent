"""
Binance WebSocket kline (OHLCV) feed.

Connects to Binance public WebSocket stream for real-time candle data.
When a candle closes (x=true), calls an on_candle_close callback which
can trigger a strategy evaluation tick in the engine.

No extra dependencies — uses stdlib `ssl`, `socket`, and a minimal
WebSocket handshake. Falls back gracefully if the connection drops.

Usage:
    feed = BinanceWebSocketFeed(
        symbols=["btcusdt", "ethusdt"],
        interval="1m",
        on_candle_close=lambda symbol, candle: engine.tick(symbol),
    )
    feed.start()
    # ... trading runs ...
    feed.stop()
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

from loguru import logger


_BINANCE_WS_HOST = "stream.binance.com"
_BINANCE_WS_PORT = 9443


class BinanceWebSocketFeed:
    """
    Subscribes to Binance combined stream for kline data.

    For each symbol, when a candle closes (kline.x == True),
    ``on_candle_close(symbol, candle_dict)`` is called from the feed thread.

    The feed thread is a daemon — it stops automatically when the main
    process exits. Call stop() for a clean shutdown.

    Args:
        symbols:          List of symbol strings in any case (e.g. "BTC/USDT" or "btcusdt").
        interval:         Kline interval ("1m", "5m", "15m", "1h", "4h", "1d").
        on_candle_close:  Callback(symbol: str, candle: dict) — called on closed candles.
        reconnect_delay:  Seconds to wait before reconnecting after a drop (default 5).
    """

    def __init__(
        self,
        symbols: list[str],
        interval: str,
        on_candle_close: Callable[[str, dict], None],
        reconnect_delay: float = 5.0,
    ) -> None:
        self._symbols = [s.replace("/", "").lower() for s in symbols]
        self._interval = interval
        self._on_candle_close = on_candle_close
        self._reconnect_delay = reconnect_delay
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the WebSocket feed thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="binance-ws-feed",
        )
        self._thread.start()
        logger.info(
            "BinanceWebSocketFeed started",
            symbols=self._symbols,
            interval=self._interval,
        )

    def stop(self) -> None:
        """Signal the feed thread to stop."""
        self._stop_event.set()
        logger.info("BinanceWebSocketFeed stop requested")

    # ── Internal: run loop with reconnect ────────────────────────────────────

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._connect_and_read()
            except Exception as exc:  # noqa: BLE001
                if self._stop_event.is_set():
                    break
                logger.warning(
                    "WebSocket connection lost, reconnecting",
                    error=str(exc),
                    delay=self._reconnect_delay,
                )
                time.sleep(self._reconnect_delay)
        logger.info("BinanceWebSocketFeed stopped")

    def _connect_and_read(self) -> None:
        """Open a single WebSocket connection and read until stop or error."""
        streams = "/".join(f"{s}@kline_{self._interval}" for s in self._symbols)
        path = f"/stream?streams={streams}"

        context = ssl.create_default_context()
        sock = socket.create_connection((_BINANCE_WS_HOST, _BINANCE_WS_PORT), timeout=10)
        sock = context.wrap_socket(sock, server_hostname=_BINANCE_WS_HOST)

        try:
            _ws_handshake(sock, _BINANCE_WS_HOST, path)
            logger.info("WebSocket connected", host=_BINANCE_WS_HOST, path=path)

            sock.settimeout(60.0)  # 60s read timeout (Binance sends pings ~20s)
            while not self._stop_event.is_set():
                frame = _ws_read_frame(sock)
                if frame is None:
                    break
                if isinstance(frame, bytes) and frame == b"":
                    continue  # ping/control frame handled internally
                self._handle_message(frame)
        finally:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass

    def _handle_message(self, raw: str | bytes) -> None:
        try:
            text = raw if isinstance(raw, str) else raw.decode("utf-8")
            msg = json.loads(text)
            data = msg.get("data", msg)
            if data.get("e") != "kline":
                return
            kline = data["k"]
            if not kline.get("x"):  # x = is_closed
                return
            symbol_raw = kline["s"]  # e.g. "BTCUSDT"
            # Normalise to "BTC/USDT" format
            symbol = _normalise_symbol(symbol_raw)
            candle = {
                "open_time": kline["t"],
                "open": float(kline["o"]),
                "high": float(kline["h"]),
                "low": float(kline["l"]),
                "close": float(kline["c"]),
                "volume": float(kline["v"]),
                "close_time": kline["T"],
            }
            logger.debug("Candle closed", symbol=symbol, close=candle["close"])
            self._on_candle_close(symbol, candle)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to handle WebSocket message", error=str(exc))


# ── Minimal WebSocket client (RFC 6455) ───────────────────────────────────────

def _ws_handshake(sock: ssl.SSLSocket, host: str, path: str) -> None:
    """Send HTTP upgrade request and validate the response."""
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode())

    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError("WebSocket handshake failed: connection closed")
        response += chunk

    if b"101" not in response:
        raise ConnectionError(f"WebSocket upgrade rejected: {response[:200]}")

    # Validate server key
    expected_accept = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode()
    if expected_accept.encode() not in response:
        raise ConnectionError("WebSocket accept key mismatch")


def _ws_read_frame(sock: ssl.SSLSocket) -> str | bytes | None:
    """
    Read one WebSocket data frame. Returns text content, b"" for control
    frames (ping/pong handled internally), or None on close frame.
    """
    header = _recv_exact(sock, 2)
    if header is None:
        return None

    fin = (header[0] & 0x80) != 0
    opcode = header[0] & 0x0F
    masked = (header[1] & 0x80) != 0
    payload_len = header[1] & 0x7F

    if payload_len == 126:
        ext = _recv_exact(sock, 2)
        if ext is None:
            return None
        payload_len = struct.unpack("!H", ext)[0]
    elif payload_len == 127:
        ext = _recv_exact(sock, 8)
        if ext is None:
            return None
        payload_len = struct.unpack("!Q", ext)[0]

    mask_key = b""
    if masked:
        mask_key = _recv_exact(sock, 4) or b""

    payload = _recv_exact(sock, payload_len) or b""
    if masked and mask_key:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

    if opcode == 0x08:  # close
        return None
    if opcode == 0x09:  # ping — send pong
        _ws_send_pong(sock, payload)
        return b""
    if opcode == 0x0A:  # pong
        return b""
    if opcode == 0x01:  # text
        return payload.decode("utf-8")
    if opcode == 0x02:  # binary
        return payload
    return b""  # continuation or unknown — skip


def _recv_exact(sock: ssl.SSLSocket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _ws_send_pong(sock: ssl.SSLSocket, payload: bytes) -> None:
    frame = bytes([0x8A, len(payload)]) + payload
    try:
        sock.sendall(frame)
    except Exception:  # noqa: BLE001
        pass


def _normalise_symbol(raw: str) -> str:
    """
    Convert Binance symbol format to ccxt format.
    "BTCUSDT" → "BTC/USDT", "ETHBTC" → "ETH/BTC"

    Handles common quote currencies: USDT, BUSD, BTC, ETH, BNB, USDC, FDUSD.
    """
    quotes = ["USDT", "BUSD", "FDUSD", "USDC", "BTC", "ETH", "BNB", "TRY", "EUR", "GBP"]
    raw = raw.upper()
    for q in quotes:
        if raw.endswith(q):
            base = raw[: -len(q)]
            return f"{base}/{q}"
    return raw  # fallback: return as-is
