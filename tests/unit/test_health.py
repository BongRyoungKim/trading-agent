"""Unit tests for src/health.py."""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from http.server import HTTPServer

import pytest

import src.health as health_module
from src.health import HealthState, _HealthHandler, get_health_state, start_health_server


def _free_port() -> int:
    """OS가 즉시 하나 배정해주는 사용 가능한 포트를 얻는다(테스트 전용)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


class TestLatencyRecording:
    def _fresh(self) -> HealthState:
        return HealthState()

    def test_no_latency_recorded_yet(self):
        s = self._fresh()
        assert s.latency_snapshot()["exchange_latency_ms"] is None
        assert s.latency_snapshot()["exchange_latency_status"] == "unknown"

    def test_fast_latency_is_ok(self):
        s = self._fresh()
        s.record_latency_ms(120.0)
        snap = s.latency_snapshot()
        assert snap["exchange_latency_ms"] == 120.0
        assert snap["exchange_latency_status"] == "ok"

    def test_slow_latency_flagged(self):
        s = self._fresh()
        s.record_latency_ms(7000.0)
        snap = s.latency_snapshot()
        assert snap["exchange_latency_ms"] == 7000.0
        assert snap["exchange_latency_status"] == "slow"

    def test_latest_value_wins(self):
        s = self._fresh()
        s.record_latency_ms(7000.0)
        s.record_latency_ms(100.0)
        snap = s.latency_snapshot()
        assert snap["exchange_latency_ms"] == 100.0
        assert snap["exchange_latency_status"] == "ok"


class TestBalanceAnomalyDetection:
    def _fresh(self) -> HealthState:
        return HealthState()

    def test_first_snapshot_never_anomaly(self):
        s = self._fresh()
        result = s.record_equity_snapshot(1_000_000.0)
        assert result is None

    def test_no_anomaly_on_small_change(self):
        s = self._fresh()
        s.record_equity_snapshot(1_000_000.0)
        result = s.record_equity_snapshot(950_000.0)  # -5%, normal drawdown noise
        assert result is None

    def test_no_anomaly_on_increase(self):
        s = self._fresh()
        s.record_equity_snapshot(1_000_000.0)
        result = s.record_equity_snapshot(2_000_000.0)
        assert result is None

    def test_anomaly_on_large_drop(self):
        s = self._fresh()
        s.record_equity_snapshot(1_000_000.0)
        result = s.record_equity_snapshot(600_000.0)  # -40%
        assert result is not None
        assert result["previous"] == 1_000_000.0
        assert result["current"] == 600_000.0
        assert result["drop_pct"] == pytest.approx(40.0)

    def test_anomaly_reflected_in_snapshot(self):
        s = self._fresh()
        s.record_equity_snapshot(1_000_000.0)
        s.record_equity_snapshot(600_000.0)
        snap = s.balance_snapshot()
        assert snap["balance_anomaly"] is True
        assert snap["balance_anomaly_detail"]["drop_pct"] == pytest.approx(40.0)

    def test_no_anomaly_reflected_in_snapshot_by_default(self):
        s = self._fresh()
        snap = s.balance_snapshot()
        assert snap["balance_anomaly"] is False
        assert snap["balance_anomaly_detail"] is None

    def test_subsequent_stable_snapshot_clears_anomaly_flag(self):
        s = self._fresh()
        s.record_equity_snapshot(1_000_000.0)
        s.record_equity_snapshot(600_000.0)  # anomaly
        s.record_equity_snapshot(590_000.0)  # small change vs new baseline — no new anomaly
        snap = s.balance_snapshot()
        assert snap["balance_anomaly"] is False


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

    def test_health_still_returns_200_with_existing_fields(self, health_server):
        """Additive fields must never change the pre-existing contract that
        Docker HEALTHCHECK relies on (always 200, status='ok', timestamp)."""
        port, _ = health_server
        get_health_state().record_latency_ms(150.0)
        code, body = _get(port, "/health")
        assert code == 200
        assert body["status"] == "ok"
        assert "timestamp" in body

    def test_health_includes_latency_fields(self, health_server):
        port, _ = health_server
        get_health_state().record_latency_ms(150.0)
        _, body = _get(port, "/health")
        assert body["exchange_latency_ms"] == 150.0
        assert body["exchange_latency_status"] == "ok"

    def test_health_includes_balance_anomaly_fields(self, health_server):
        port, _ = health_server
        _, body = _get(port, "/health")
        assert "balance_anomaly" in body
        assert isinstance(body["balance_anomaly"], bool)
        assert "balance_anomaly_detail" in body


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


# ── Watchdog self-healing ─────────────────────────────────────────────────────

class TestWatchdog:
    def test_shutdown_disables_watchdog_no_respawn(self, monkeypatch):
        """의도적인 shutdown()은 watchdog까지 같이 멈춰야 한다(되살아나면 안 됨)."""
        created: list[HTTPServer] = []
        real_ctor = health_module.HTTPServer

        def tracking_ctor(*args, **kwargs):
            srv = real_ctor(*args, **kwargs)
            created.append(srv)
            return srv

        monkeypatch.setattr(health_module, "HTTPServer", tracking_ctor)

        port = _free_port()
        server = start_health_server(host="127.0.0.1", port=port, watchdog_interval=0.05)
        server.shutdown()
        time.sleep(0.3)  # watchdog이 잘못 재기동할 시간을 넉넉히 준다

        assert len(created) == 1  # 재기동이 일어나지 않았어야 함

    def test_watchdog_respawns_after_unexpected_crash(self, monkeypatch):
        """서빙 스레드가 예기치 않게 죽어도 watchdog이 자동으로 되살려야 한다.

        실제 사고 재현을 위해 실소켓을 깨는 방식은 select() 루프가 예외 없이
        그냥 응답을 멈추기만 할 뿐 스레드를 안 죽여서 신뢰성 있게 재현되지
        않는다(별도로 확인함). 대신 최초 인스턴스의 serve_forever() 자체가
        예외를 던지도록 만들어 "서빙 스레드가 죽는" 상황을 결정적으로 재현한다.
        """
        created: list[HTTPServer] = []
        real_ctor = health_module.HTTPServer

        def tracking_ctor(*args, **kwargs):
            srv = real_ctor(*args, **kwargs)
            created.append(srv)
            if len(created) == 1:
                def _boom(poll_interval: float = 0.5) -> None:
                    raise OSError("simulated crash")
                srv.serve_forever = _boom  # type: ignore[method-assign]
            return srv

        monkeypatch.setattr(health_module, "HTTPServer", tracking_ctor)

        port = _free_port()
        server = start_health_server(host="127.0.0.1", port=port, watchdog_interval=0.05)
        assert server is created[0]
        try:
            time.sleep(0.5)  # watchdog이 죽은 스레드를 감지하고 재기동할 시간

            assert len(created) >= 2, "watchdog이 재기동하지 않음"
            code, body = _get(port, "/health")
            assert code == 200
            assert body["status"] == "ok"
        finally:
            # created[0]는 serve_forever를 통째로 갈아끼워서 이미 죽였기 때문에
            # (내부 __is_shut_down 이벤트가 세팅될 일이 없어) shutdown()을 부르면
            # 영원히 블록된다 — server_close()는 크래시 시점에 이미 호출됐으므로
            # 건드릴 필요가 없다. 실제로 살아있는 이후 인스턴스만 정리한다.
            for srv in created[1:]:
                try:
                    srv.shutdown()
                except Exception:  # noqa: BLE001
                    pass
