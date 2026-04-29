"""Unit tests for the web dashboard (FastAPI app + DashboardState)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.dashboard.state import DashboardState, get_dashboard_state
from src.dashboard.templates import render_dashboard


# ── DashboardState ────────────────────────────────────────────────────────────

class TestDashboardState:
    def _state(self) -> DashboardState:
        return DashboardState()

    def _mock_engine(self, paused: bool = False, mode: str = "paper") -> MagicMock:
        eng = MagicMock()
        eng.mode = mode
        eng.is_paused = paused
        cb = MagicMock()
        cb.state.name = "CLOSED"
        eng.circuit_breaker = cb
        mh = MagicMock()
        mh.enabled = False
        mh.trading_hours = "00:00-23:59"
        mh.trading_days = "mon-sun"
        eng.market_hours = mh
        # Portfolio mock
        portfolio = MagicMock()
        portfolio.pnl_report.return_value = {
            "cash": 10000.0,
            "realized_pnl": 0.0,
            "open_positions": 0,
        }
        portfolio.open_symbols.return_value = []
        eng._portfolio = portfolio  # noqa: SLF001
        return eng

    def test_get_status_no_engine_returns_not_ready(self) -> None:
        state = self._state()
        status = state.get_status()
        assert status["ready"] is False

    def test_get_status_with_engine_shows_mode(self) -> None:
        state = self._state()
        state.register_engine(self._mock_engine(mode="live"))
        status = state.get_status()
        assert status["ready"] is True
        assert status["mode"] == "live"

    def test_get_status_reflects_paused_state(self) -> None:
        state = self._state()
        state.register_engine(self._mock_engine(paused=True))
        assert state.get_status()["paused"] is True

    def test_get_positions_empty_without_engine(self) -> None:
        state = self._state()
        assert state.get_positions() == []

    def test_get_pnl_empty_without_engine(self) -> None:
        state = self._state()
        assert state.get_pnl() == {}

    def test_get_pnl_with_engine(self) -> None:
        state = self._state()
        state.register_engine(self._mock_engine())
        pnl = state.get_pnl()
        assert "cash" in pnl
        assert pnl["cash"] == 10000.0

    def test_pause_delegates_to_engine(self) -> None:
        state = self._state()
        eng = self._mock_engine()
        state.register_engine(eng)
        state.pause()
        eng.pause.assert_called_once()

    def test_resume_delegates_to_engine(self) -> None:
        state = self._state()
        eng = self._mock_engine()
        state.register_engine(eng)
        state.resume()
        eng.resume.assert_called_once()

    def test_pause_noop_without_engine(self) -> None:
        """Should not raise if no engine is registered."""
        state = self._state()
        state.pause()  # no exception

    def test_register_engine_thread_safe(self) -> None:
        """register_engine can be called from any thread."""
        import threading
        state = self._state()
        eng = self._mock_engine()
        errors = []

        def register() -> None:
            try:
                state.register_engine(eng)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=register) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert state.engine is eng


# ── HTML Template ─────────────────────────────────────────────────────────────

class TestRenderDashboard:
    def _status(self, paused: bool = False) -> dict:
        return {
            "ready": True,
            "mode": "paper",
            "paused": paused,
            "circuit": "CLOSED",
            "market_hours_enabled": False,
            "trading_hours": "24/7",
            "trading_days": "all",
        }

    def test_render_returns_html_string(self) -> None:
        html = render_dashboard(self._status(), [], {})
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_html_contains_auto_refresh_script(self) -> None:
        html = render_dashboard(self._status(), [], {})
        assert "setInterval" in html
        assert "30000" in html

    def test_html_contains_pause_resume_buttons(self) -> None:
        html = render_dashboard(self._status(), [], {})
        assert "pause" in html.lower()
        assert "resume" in html.lower()

    def test_html_shows_mode(self) -> None:
        html = render_dashboard(self._status(), [], {})
        assert "PAPER" in html

    def test_html_shows_paused_state(self) -> None:
        html = render_dashboard(self._status(paused=True), [], {})
        assert "PAUSED" in html

    def test_html_shows_running_state(self) -> None:
        html = render_dashboard(self._status(paused=False), [], {})
        assert "RUNNING" in html

    def test_html_no_positions_shows_empty_message(self) -> None:
        html = render_dashboard(self._status(), [], {})
        assert "오픈 포지션 없음" in html

    def test_html_shows_position_symbol(self) -> None:
        positions = [{
            "symbol": "BTC/USDT",
            "side": "buy",
            "amount": 0.01,
            "entry_price": 50000.0,
            "stop_loss": 48000.0,
            "take_profit": None,
        }]
        html = render_dashboard(self._status(), positions, {})
        assert "BTC/USDT" in html
        assert "50,000.00" in html

    def test_html_pnl_section(self) -> None:
        pnl = {"cash": 9500.0, "realized_pnl": -500.0, "unrealized_pnl": 100.0, "open_positions": 1}
        html = render_dashboard(self._status(), [], pnl)
        assert "9,500.00" in html
        assert "-500.00" in html

    def test_html_no_external_cdn(self) -> None:
        html = render_dashboard(self._status(), [], {})
        assert "cdn.jsdelivr" not in html
        assert "cdnjs.cloudflare" not in html
        assert "unpkg.com" not in html


# ── FastAPI routes ─────────────────────────────────────────────────────────────

@pytest.fixture()
def client():
    """TestClient with a mocked dashboard state."""
    from fastapi.testclient import TestClient
    from src.dashboard.app import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_dashboard_state() -> None:
    """Reset dashboard singleton before each test."""
    state = get_dashboard_state()
    state._engine = None  # noqa: SLF001


class TestFastAPIRoutes:
    def test_get_root_returns_200_html(self, client) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_get_status_returns_200(self, client) -> None:
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "ready" in data

    def test_get_positions_returns_list(self, client) -> None:
        resp = client.get("/api/positions")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_pnl_returns_dict(self, client) -> None:
        resp = client.get("/api/pnl")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_post_pause_no_engine_returns_503(self, client) -> None:
        resp = client.post("/api/engine/pause")
        assert resp.status_code == 503

    def test_post_resume_no_engine_returns_503(self, client) -> None:
        resp = client.post("/api/engine/resume")
        assert resp.status_code == 503

    def test_post_pause_with_engine_returns_200(self, client) -> None:
        state = get_dashboard_state()
        eng = MagicMock()
        eng.mode = "paper"
        eng.is_paused = False
        eng.circuit_breaker.state.name = "CLOSED"
        eng.market_hours.enabled = False
        eng.market_hours.trading_hours = "00:00-23:59"
        eng.market_hours.trading_days = "mon-sun"
        eng._portfolio.pnl_report.return_value = {"cash": 10000, "realized_pnl": 0, "open_positions": 0}  # noqa: SLF001
        eng._portfolio.open_symbols.return_value = []  # noqa: SLF001
        state.register_engine(eng)
        resp = client.post("/api/engine/pause")
        assert resp.status_code == 200
        eng.pause.assert_called_once()

    def test_get_trades_returns_list(self, client) -> None:
        resp = client.get("/api/trades")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_stats_returns_dict(self, client) -> None:
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_get_equity_returns_list(self, client) -> None:
        resp = client.get("/api/equity")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_trades_limit_param(self, client) -> None:
        resp = client.get("/api/trades?limit=10")
        assert resp.status_code == 200

    def test_post_resume_with_engine_returns_200(self, client) -> None:
        state = get_dashboard_state()
        eng = MagicMock()
        eng.mode = "paper"
        eng.is_paused = True
        eng.circuit_breaker.state.name = "CLOSED"
        eng.market_hours.enabled = False
        eng.market_hours.trading_hours = "00:00-23:59"
        eng.market_hours.trading_days = "mon-sun"
        eng._portfolio.pnl_report.return_value = {"cash": 10000, "realized_pnl": 0, "open_positions": 0}  # noqa: SLF001
        eng._portfolio.open_symbols.return_value = []  # noqa: SLF001
        state.register_engine(eng)
        resp = client.post("/api/engine/resume")
        assert resp.status_code == 200
        eng.resume.assert_called_once()
