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

자가복구(watchdog)
------------------
운영 중 이 서버의 서빙 스레드가 원인 불명의 예외로 죽어버리는 사고가 있었다
(리스닝 소켓 자체는 정리되지 않은 채 남아, 이후의 모든 헬스체크 요청이
"connection refused"가 아니라 타임아웃으로 계속 실패 — 도커 컨테이너가
장시간 "unhealthy"로 표시됨). 정확한 최초 원인은 사후에 확인하지 못했다
(발생 시점의 예외가 loguru가 아닌 스레드 기본 예외 훅으로만 stderr에
찍혀서, 컨테이너 재생성으로 유실되는 `docker compose logs`에만 남고
파일 로그에는 남지 않았을 가능성이 높다).

같은 사고가 재발해도 컨테이너가 장시간 unhealthy로 멈춰있지 않도록,
서빙 스레드를 감시하는 별도의 데몬 스레드(watchdog)를 둔다: 스레드가
죽은 게 감지되면 소켓을 정리하고 즉시 재기동한다. 원인 규명이 안 된
문제에 대한 임시방편이 아니라, "단일 백그라운드 스레드가 죽으면
전체 헬스체크가 무한정 죽는다"는 구조적 취약점 자체를 없애는 것이 목적이다.
"""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from loguru import logger

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
        """요청 처리 중 어떤 예외가 나도 서빙 스레드 자체는 죽지 않게 감싼다.

        표준 라이브러리의 서버 루프도 핸들러 예외를 내부적으로 잡아 계속
        서빙하긴 하지만, 이 레이어에서도 한 번 더 방어해 헬스체크만큼은
        단일 요청의 실패가 서버 전체에 영향을 주지 않도록 한다.
        """
        try:
            self._do_GET()
        except Exception as exc:  # noqa: BLE001
            logger.error("Health handler error", error=str(exc), path=self.path)
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:  # noqa: BLE001
                pass

    def _do_GET(self) -> None:
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


# ── Server lifecycle ────────────────────────────────────────────────────────

def _serve_forever(server: HTTPServer) -> None:
    """`server.serve_forever()`를 돌리고, 어떻게 끝나든 소켓을 정리한다.

    정상적인 `server.shutdown()` 호출이든, 예상 못 한 예외든 `finally`에서
    `server_close()`를 호출해 소켓을 확실히 반환한다 — 그래야 watchdog가
    같은 포트로 곧바로 재기동할 수 있다(안 그러면 죽은 소켓이 포트를 계속
    붙잡고 있어 재기동 시도조차 "address in use"로 실패할 수 있다).
    """
    try:
        server.serve_forever()
    except Exception as exc:  # noqa: BLE001
        logger.error("Health server thread crashed", error=str(exc))
    finally:
        try:
            server.server_close()
        except Exception:  # noqa: BLE001
            pass


def start_health_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    watchdog_interval: float = 30.0,
) -> HTTPServer:
    """
    Start the health HTTP server in a daemon thread, with a self-healing watchdog.

    Args:
        host: Bind address (default ``"0.0.0.0"``).
        port: Listening port (default ``8080``).
        watchdog_interval: 서빙 스레드 생존 여부를 확인하는 주기(초).
            0 이하로 주면 watchdog을 비활성화한다(테스트 등에서 사용).

    Returns:
        The running ``HTTPServer`` instance (call ``.shutdown()`` to stop it —
        watchdog도 함께 멈춘다).
    """
    stop_event = threading.Event()
    state: dict[str, object] = {}

    def spawn() -> None:
        server = HTTPServer((host, port), _HealthHandler)
        original_shutdown = server.shutdown

        def _shutdown_and_stop_watchdog() -> None:
            # 의도적인 종료다 — watchdog이 이걸 "장애"로 오인해 되살리지 않게 한다.
            stop_event.set()
            original_shutdown()

        server.shutdown = _shutdown_and_stop_watchdog  # type: ignore[method-assign]

        thread = threading.Thread(
            target=_serve_forever, args=(server,), daemon=True, name="health-server"
        )
        state["server"] = server
        state["thread"] = thread
        thread.start()

    spawn()
    logger.info("Health server started", host=host, port=port)

    if watchdog_interval > 0:

        def _watchdog() -> None:
            while not stop_event.wait(watchdog_interval):
                thread = state.get("thread")
                if thread is not None and not thread.is_alive():
                    logger.warning(
                        "Health server thread found dead — restarting", port=port
                    )
                    try:
                        spawn()
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Health server restart failed", error=str(exc))

        threading.Thread(
            target=_watchdog, daemon=True, name="health-server-watchdog"
        ).start()

    return state["server"]  # type: ignore[return-value]
