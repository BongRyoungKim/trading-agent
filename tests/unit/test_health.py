"""Unit tests for src/health.py."""
from __future__ import annotations

import json
import threading
import urllib.request
from http.server import HTTPServer

import pytest

from src.health import HealthState, _HealthHandler, get_health_state, start_health_server


# ── HealthState ───────────────────────────────────────────────────────────────

class TestHealthState:
    def _fresh(self) -> HealthState:
        return HealthState()

    def test_initial_not_ready(self):
        s = self._fresh()
        assert not s.is_ready

    def test_set_ready(self):
        s = self._fresh()
        s.set_ready()
        assert s.is_ready

    def test_set_ready_with_details(self):
        s = self._fresh()
        s.set_ready({"mode": "paper"})
        assert s.snapshot()["mode"] == "paper"

    def test_set_not_ready_after_ready(self):
        s = self._fresh()
        s.set_ready()
        s.set_not_ready("shutting down")
        assert not s.is_ready

    def test_set_not_ready_includes_reason(self):
        s = self._fresh()
        s.set_not_ready("engine stopped")
        assert s.snapshot()["reason"] == "engine stopped"

    def test_set_not_ready_no_reason_empty_details(self):
        s = self._fresh()
        s.set_not_ready()
        assert s.snapshot() == {}

    def test_snapshot_is_copy(self):
        """Mutating the snapshot does not affect internal state."""
        s = self._fresh()
        s.set_ready({"key": "value"})
        snap = s.snapshot()
        snap["key"] = "mutated"
        assert s.snapshot()["key"] == "value"

    def test_thread_safe_concurrent_writes(self):
        """Multiple threads writing do not corrupt state."""
        s = self._fresh()
        errors: list[Exception] = []

        def toggle():
            try:
                for _ in range(100):
                    s.set_ready({"x": 1})
                    s.set_not_ready("x")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=toggle) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors

    def test_get_health_state_returns_singleton(self):
        assert get_health_state() is get_health_state()


# ── HTTP endpoints ────────────────────────────────────────────────────────────

@pytest.fixture()
def health_server():
    """Start a test health server on a random port and tear it down after."""
    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port, server
    server.shutdown()


def _get(port: int, path: str) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class TestHealthEndpoint:
    def test_health_returns_200(self, health_server):
        port, _ = health_server
        code, body = _get(port, "/health")
        assert code == 200

    def test_health_body_has_status_ok(self, health_server):
        port, _ = health_server
        _, body = _get(port, "/health")
        assert body["status"] == "ok"

    def test_health_body_has_timestamp(self, health_server):
        port, _ = health_server
        _, body = _get(port, "/health")
        assert "timestamp" in body

    def test_unknown_path_returns_404(self, health_server):
        port, _ = health_server
        code, _ = _get(port, "/unknown")
        assert code == 404


class TestReadyEndpoint:
    def setup_method(self):
        # Reset global singleton before each test
        get_health_state().set_not_ready()

    def test_not_ready_returns_503(self, health_server):
        port, _ = health_server
        get_health_state().set_not_ready()
        code, _ = _get(port, "/ready")
        assert code == 503

    def test_not_ready_body_has_status(self, health_server):
        port, _ = health_server
        get_health_state().set_not_ready()
        _, body = _get(port, "/ready")
        assert body["status"] == "not_ready"

    def test_ready_returns_200(self, health_server):
        port, _ = health_server
        get_health_state().set_ready()
        code, _ = _get(port, "/ready")
        assert code == 200

    def test_ready_body_has_status(self, health_server):
        port, _ = health_server
        get_health_state().set_ready()
        _, body = _get(port, "/ready")
        assert body["status"] == "ready"

    def test_ready_details_included(self, health_server):
        port, _ = health_server
        get_health_state().set_ready({"mode": "paper", "exchange": "binance"})
        _, body = _get(port, "/ready")
        assert body["mode"] == "paper"
        assert body["exchange"] == "binance"

    def test_not_ready_reason_included(self, health_server):
        port, _ = health_server
        get_health_state().set_not_ready("engine stopped")
        _, body = _get(port, "/ready")
        assert body["reason"] == "engine stopped"


# ── start_health_server ───────────────────────────────────────────────────────

class TestStartHealthServer:
    def test_returns_httpserver(self):
        server = start_health_server(host="127.0.0.1", port=0)
        try:
            assert isinstance(server, HTTPServer)
        finally:
            server.shutdown()

    def test_server_responds_to_health(self):
        server = start_health_server(host="127.0.0.1", port=0)
        port = server.server_address[1]
        try:
            code, body = _get(port, "/health")
            assert code == 200
            assert body["status"] == "ok"
        finally:
            server.shutdown()
