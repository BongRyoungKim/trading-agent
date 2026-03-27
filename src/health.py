"""
Lightweight HTTP health-check server.

Exposes two endpoints so container orchestrators (Docker, Kubernetes) can
probe the trading agent:

    GET /health  →  200 {"status": "ok", "timestamp": "..."}
                    Always 200 while the process is alive.

    GET /ready   →  200 {"status": "ready", ...details}   — engine running
                    503 {"status": "not_ready", ...details} — engine not ready

Intended usage::

    from src.health import get_health_state, start_health_server

    server = start_health_server(port=8080)
    # ... later, when the engine is ready:
    get_health_state().set_ready({"mode": "paper", "symbols": ["BTC/USDT"]})
    # ... on shutdown:
    get_health_state().set_not_ready("shutting down")
"""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer


# ── Health state ──────────────────────────────────────────────────────────────

class HealthState:
    """Thread-safe container for engine readiness state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = False
        self._details: dict = {}

    def set_ready(self, details: dict | None = None) -> None:
        """Mark the engine as ready. Optional *details* appear in /ready response."""
        with self._lock:
            self._ready = True
            self._details = dict(details) if details else {}

    def set_not_ready(self, reason: str = "") -> None:
        """Mark the engine as not ready."""
        with self._lock:
            self._ready = False
            self._details = {"reason": reason} if reason else {}

    @property
    def is_ready(self) -> bool:
        with self._lock:
            return self._ready

    def snapshot(self) -> dict:
        """Return a copy of the current detail dict."""
        with self._lock:
            return dict(self._details)


# Module-level singleton — the engine and main.py share this instance.
_health_state = HealthState()


def get_health_state() -> HealthState:
    """Return the global HealthState singleton."""
    return _health_state


# ── HTTP handler ──────────────────────────────────────────────────────────────

class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal GET handler for /health and /ready."""

    def do_GET(self) -> None:  # noqa: N802
        state = get_health_state()

        if self.path == "/health":
            body = json.dumps(
                {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}
            ).encode()
            code = 200
        elif self.path == "/ready":
            if state.is_ready:
                payload = {"status": "ready", **state.snapshot()}
                code = 200
            else:
                payload = {"status": "not_ready", **state.snapshot()}
                code = 503
            body = json.dumps(payload).encode()
        else:
            body = b'{"error": "not found"}'
            code = 404

        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D102
        """Suppress default stderr access log."""


# ── Server lifecycle ──────────────────────────────────────────────────────────

def start_health_server(host: str = "0.0.0.0", port: int = 8080) -> HTTPServer:
    """
    Start the health HTTP server in a daemon thread.

    Args:
        host: Bind address (default ``"0.0.0.0"``).
        port: Listening port (default ``8080``).

    Returns:
        The running ``HTTPServer`` instance (call ``.shutdown()`` to stop it).
    """
    server = HTTPServer((host, port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="health-server")
    thread.start()
    return server
