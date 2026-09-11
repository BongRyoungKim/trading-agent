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

    def test_get_positions_includes_highest_price(self) -> None:
        from datetime import UTC, datetime
        from decimal import Decimal

        state = self._state()
        eng = self._mock_engine()
        pos = MagicMock()
        pos.side = "buy"
        pos.amount = Decimal("0.1")
        pos.entry_price = Decimal("50000")
        pos.entry_time = datetime(2024, 1, 1, tzinfo=UTC)
        pos.stop_loss = Decimal("48000")
        pos.take_profit = Decimal("55000")
        pos.highest_price = Decimal("53000")
        eng._portfolio.open_symbols.return_value = ["BTC/USDT"]  # noqa: SLF001
        eng._portfolio.get_position.return_value = pos  # noqa: SLF001
        eng._latest_ticks = {}  # noqa: SLF001
        state.register_engine(eng)

        positions = state.get_positions()
        assert len(positions) == 1
        assert positions[0]["highest_price"] == 53000.0

    def test_get_positions_highest_price_none_when_unset(self) -> None:
        from datetime import UTC, datetime
        from decimal import Decimal

        state = self._state()
        eng = self._mock_engine()
        pos = MagicMock()
        pos.side = "buy"
        pos.amount = Decimal("0.1")
        pos.entry_price = Decimal("50000")
        pos.entry_time = datetime(2024, 1, 1, tzinfo=UTC)
        pos.stop_loss = None
        pos.take_profit = None
        pos.highest_price = None
        eng._portfolio.open_symbols.return_value = ["BTC/USDT"]  # noqa: SLF001
        eng._portfolio.get_position.return_value = pos  # noqa: SLF001
        eng._latest_ticks = {}  # noqa: SLF001
        state.register_engine(eng)

        positions = state.get_positions()
        assert positions[0]["highest_price"] is None

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


# ── Pending Parameter Change ──────────────────────────────────────────────────

class TestPendingParamChange:
    @pytest.fixture(autouse=True)
    def isolate_live_params(self, tmp_path, monkeypatch):
        import src.config.live_params as lp
        monkeypatch.setattr(lp, "PARAMS_FILE", tmp_path / ".strategy_params.json")
        monkeypatch.setattr(lp, "AUDIT_LOG", tmp_path / "param_change_log.jsonl")
        monkeypatch.setattr(lp, "RESTART_FLAG", tmp_path / ".restart_requested")
        monkeypatch.setattr(lp, "PENDING_FILE", tmp_path / "pending_param_change.json")
        monkeypatch.setattr(lp, "VALIDATION_FILE", tmp_path / "pending_param_validation.json")
        return tmp_path

    def test_no_pending_change(self):
        state = DashboardState()
        result = state.get_pending_param_change()
        assert result == {"has_pending": False}

    def test_pending_without_validation(self):
        import src.config.live_params as lp
        lp.propose_pending("mr_vol_mult", 2.2, trigger="wr_alert", reason="WR low")

        state = DashboardState()
        result = state.get_pending_param_change()
        assert result["has_pending"] is True
        assert result["strategy_params"]["mr_vol_mult"] == pytest.approx(2.2)
        assert result["validation"] is None

    def test_pending_with_validation(self):
        import src.config.live_params as lp
        lp.propose_pending("mr_vol_mult", 2.2, trigger="wr_alert", reason="WR low")
        lp.save_validation_result(
            passed=True, checks=[{"name": "profit_factor", "passed": True, "detail": "ok"}],
            baseline_metrics={"profit_factor": 1.0}, candidate_metrics={"profit_factor": 1.2},
        )

        state = DashboardState()
        result = state.get_pending_param_change()
        assert result["validation"]["passed"] is True
        assert result["validation"]["candidate_metrics"]["profit_factor"] == 1.2

    def test_approve_without_validation_refuses(self):
        import src.config.live_params as lp
        lp.propose_pending("mr_vol_mult", 2.2, trigger="wr_alert", reason="WR low")

        state = DashboardState()
        result = state.approve_pending_param_change()
        assert result["success"] is False
        assert lp.get_current("mr_vol_mult") == lp.DEFAULTS["strategy_params"]["mr_vol_mult"]

    def test_approve_with_failed_validation_refuses(self):
        import src.config.live_params as lp
        lp.propose_pending("mr_vol_mult", 2.2, trigger="wr_alert", reason="WR low")
        lp.save_validation_result(passed=False, checks=[], baseline_metrics={}, candidate_metrics={})

        state = DashboardState()
        result = state.approve_pending_param_change()
        assert result["success"] is False
        assert lp.has_pending_change()  # untouched, still there for a human to reconsider

    def test_approve_with_passed_validation_applies_live(self):
        import src.config.live_params as lp
        lp.propose_pending("mr_vol_mult", 2.2, trigger="wr_alert", reason="WR low")
        lp.save_validation_result(passed=True, checks=[], baseline_metrics={}, candidate_metrics={})

        state = DashboardState()
        result = state.approve_pending_param_change()
        assert result["success"] is True
        assert lp.get_current("mr_vol_mult") == pytest.approx(2.2)
        assert not lp.has_pending_change()
        assert lp.restart_requested()

    def test_reject_clears_pending_and_validation(self):
        import src.config.live_params as lp
        lp.propose_pending("mr_vol_mult", 2.2, trigger="wr_alert", reason="WR low")
        lp.save_validation_result(passed=True, checks=[], baseline_metrics={}, candidate_metrics={})

        state = DashboardState()
        result = state.reject_pending_param_change()
        assert result["success"] is True
        assert not lp.has_pending_change()
        assert lp.load_validation_result() is None
        assert lp.get_current("mr_vol_mult") == lp.DEFAULTS["strategy_params"]["mr_vol_mult"]


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

    def test_html_signal_row_click_opens_upbit(self) -> None:
        """신호평가 목록 행 클릭 시 새 창으로 업비트 해당 코인 페이지로 이동해야 한다."""
        html = render_dashboard(self._status(), [], {})
        assert "function openUpbit(symbol)" in html
        assert "upbit.com/exchange?code=CRIX.UPBIT." in html
        assert "window.open(url, '_blank', 'noopener,noreferrer')" in html
        assert "onclick=\"openUpbit('${t.symbol}')\"" in html

    def test_html_no_external_cdn(self) -> None:
        html = render_dashboard(self._status(), [], {})
        assert "cdn.jsdelivr" not in html
        assert "cdnjs.cloudflare" not in html
        assert "unpkg.com" not in html

    # ── UX improvements: trade filters / symbol P&L chart / theme toggle ──────

    def _trade(self, symbol: str = "BTC/KRW", pnl: float = 100_000.0, is_win: bool = True) -> dict:
        return {
            "symbol": symbol,
            "amount": 0.01,
            "entry_price": 50_000_000.0,
            "entry_time": "2026-09-01T10:00:00",
            "exit_price": 51_000_000.0,
            "exit_time": "2026-09-02T10:00:00",
            "pnl": pnl,
            "pnl_pct": 2.0,
            "is_win": is_win,
            "reason": "take_profit",
        }

    def test_html_trade_filter_controls_present(self) -> None:
        html = render_dashboard(self._status(), [], {}, trades=[self._trade()])
        assert 'id="filter-period"' in html
        assert 'id="filter-symbol"' in html
        assert 'id="filter-result"' in html
        assert "function applyTradeFilters()" in html
        assert "function _filterTrades(trades)" in html
        assert "function _populateSymbolFilter(trades)" in html

    def test_html_trade_filter_symbol_options_seeded_from_trades(self) -> None:
        html = render_dashboard(
            self._status(), [], {}, trades=[self._trade(symbol="ETH/KRW")]
        )
        assert '<option value="ETH/KRW">ETH/KRW</option>' in html

    def test_html_symbol_pnl_chart_present(self) -> None:
        trades = [self._trade(symbol="BTC/KRW", pnl=100_000.0), self._trade(symbol="ETH/KRW", pnl=-50_000.0)]
        html = render_dashboard(self._status(), [], {}, trades=trades)
        assert "심볼별 손익 비교" in html
        assert 'id="symbol-pnl-wrap"' in html
        assert "function renderSymbolPnl(trades)" in html
        assert "<svg" in html  # chart actually rendered, not just empty-state text

    def test_html_symbol_pnl_chart_empty_state(self) -> None:
        html = render_dashboard(self._status(), [], {}, trades=[])
        assert "심볼별 손익 비교" in html
        assert "거래 없음" in html

    def test_html_theme_toggle_present(self) -> None:
        html = render_dashboard(self._status(), [], {})
        assert 'id="theme-toggle"' in html
        assert 'onclick="toggleTheme()"' in html
        assert "function toggleTheme()" in html
        assert "data-theme" in html
        assert "dashboard-theme" in html  # localStorage key


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

    def test_get_params_pending_no_change(self, client, monkeypatch, tmp_path) -> None:
        import src.config.live_params as lp
        monkeypatch.setattr(lp, "PENDING_FILE", tmp_path / "pending_param_change.json")
        resp = client.get("/api/params/pending")
        assert resp.status_code == 200
        assert resp.json() == {"has_pending": False}

    def test_post_params_approve_without_validation_returns_409(self, client, monkeypatch, tmp_path) -> None:
        import src.config.live_params as lp
        monkeypatch.setattr(lp, "PARAMS_FILE", tmp_path / ".strategy_params.json")
        monkeypatch.setattr(lp, "AUDIT_LOG", tmp_path / "param_change_log.jsonl")
        monkeypatch.setattr(lp, "RESTART_FLAG", tmp_path / ".restart_requested")
        monkeypatch.setattr(lp, "PENDING_FILE", tmp_path / "pending_param_change.json")
        monkeypatch.setattr(lp, "VALIDATION_FILE", tmp_path / "pending_param_validation.json")
        lp.propose_pending("mr_vol_mult", 2.2, trigger="t", reason="r")

        resp = client.post("/api/params/approve")
        assert resp.status_code == 409
        assert resp.json()["success"] is False

    def test_post_params_reject_returns_200(self, client, monkeypatch, tmp_path) -> None:
        import src.config.live_params as lp
        monkeypatch.setattr(lp, "PENDING_FILE", tmp_path / "pending_param_change.json")
        monkeypatch.setattr(lp, "VALIDATION_FILE", tmp_path / "pending_param_validation.json")
        resp = client.post("/api/params/reject")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

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
