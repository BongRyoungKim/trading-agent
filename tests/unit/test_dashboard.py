"""Unit tests for the web dashboard (FastAPI app + DashboardState)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.dashboard.state import DashboardState, get_dashboard_state
from src.dashboard.templates import _render_equity_svg, _render_symbol_pnl_svg, render_dashboard


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
        from decimal import Decimal
        portfolio = MagicMock()
        portfolio.pnl_report.return_value = {
            "cash": 10000.0,
            "realized_pnl": 0.0,
            "open_positions": 0,
        }
        portfolio.open_symbols.return_value = []
        portfolio.initial_cash = Decimal("10000.0")
        eng._portfolio = portfolio  # noqa: SLF001
        eng.journal.trades = []
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

    def test_get_pnl_preserves_legacy_keys(self) -> None:
        """기존 키(cash/realized_pnl/unrealized_pnl/open_positions)는 하위호환을
        위해 그대로 남아 있어야 한다."""
        state = self._state()
        state.register_engine(self._mock_engine())
        pnl = state.get_pnl()
        for key in ("cash", "realized_pnl", "unrealized_pnl", "open_positions"):
            assert key in pnl

    def test_get_pnl_no_open_positions_coin_value_zero(self) -> None:
        state = self._state()
        state.register_engine(self._mock_engine())
        pnl = state.get_pnl()
        assert pnl["coin_value"] == 0.0
        assert pnl["total_balance"] == pnl["cash"]

    def test_get_pnl_coin_value_sums_open_position_market_value(self) -> None:
        from datetime import UTC, datetime
        from decimal import Decimal

        state = self._state()
        eng = self._mock_engine()
        pos = MagicMock()
        pos.symbol = "BTC/USDT"
        pos.side = "buy"
        pos.amount = Decimal("0.1")
        pos.entry_price = Decimal("50000")
        pos.entry_time = datetime(2024, 1, 1, tzinfo=UTC)
        pos.stop_loss = None
        pos.take_profit = None
        pos.highest_price = None
        eng._portfolio.open_symbols.return_value = ["BTC/USDT"]  # noqa: SLF001
        eng._portfolio.get_position.return_value = pos  # noqa: SLF001
        eng._latest_ticks = {"BTC/USDT": {"metadata": {"price": 52000.0}}}  # noqa: SLF001
        state.register_engine(eng)

        pnl = state.get_pnl()
        # coin_value = current_price(52000) * amount(0.1) = 5200
        assert pnl["coin_value"] == pytest.approx(5200.0)
        assert pnl["total_balance"] == pytest.approx(pnl["cash"] + 5200.0)
        # unrealized_pnl = (52000-50000) * 0.1 = 200
        assert pnl["unrealized_pnl"] == pytest.approx(200.0)

    def test_get_pnl_unrealized_pnl_not_zeroed_by_pnl_report_bug(self) -> None:
        """pnl_report()는 prices 인자 없이 호출돼 unrealized_pnl이 항상 0으로 깨지는
        기존 버그가 있다. get_pnl()은 그 깨진 값을 쓰지 않고 틱 캐시 기반 현재가로
        직접 재계산해야 한다 — pnl_report()가 0을 반환해도 결과는 0이 아니어야 함."""
        from datetime import UTC, datetime
        from decimal import Decimal

        state = self._state()
        eng = self._mock_engine()
        eng._portfolio.pnl_report.return_value = {  # noqa: SLF001
            "cash": 10000.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,  # pnl_report()는 항상 0을 반환(버그)
            "open_positions": 1,
        }
        pos = MagicMock()
        pos.symbol = "BTC/USDT"
        pos.side = "buy"
        pos.amount = Decimal("0.1")
        pos.entry_price = Decimal("50000")
        pos.entry_time = datetime(2024, 1, 1, tzinfo=UTC)
        pos.stop_loss = None
        pos.take_profit = None
        pos.highest_price = None
        eng._portfolio.open_symbols.return_value = ["BTC/USDT"]  # noqa: SLF001
        eng._portfolio.get_position.return_value = pos  # noqa: SLF001
        eng._latest_ticks = {"BTC/USDT": {"metadata": {"price": 45000.0}}}  # noqa: SLF001
        state.register_engine(eng)

        pnl = state.get_pnl()
        # (45000-50000) * 0.1 = -500 — pnl_report()의 0이 아니라 실제 손실이 나와야 함
        assert pnl["unrealized_pnl"] == pytest.approx(-500.0)

    def test_get_pnl_total_return_and_pct(self) -> None:
        state = self._state()
        eng = self._mock_engine()
        eng._portfolio.pnl_report.return_value = {  # noqa: SLF001
            "cash": 11000.0,
            "realized_pnl": 1000.0,
            "open_positions": 0,
        }
        state.register_engine(eng)

        pnl = state.get_pnl()
        assert pnl["initial_cash"] == pytest.approx(10000.0)
        assert pnl["total_return"] == pytest.approx(1000.0)
        assert pnl["total_return_pct"] == pytest.approx(10.0)

    def test_get_pnl_total_return_pct_zero_when_initial_cash_zero(self) -> None:
        from decimal import Decimal

        state = self._state()
        eng = self._mock_engine()
        eng._portfolio.initial_cash = Decimal("0")  # noqa: SLF001
        state.register_engine(eng)

        pnl = state.get_pnl()
        assert pnl["total_return_pct"] == 0.0

    def test_get_pnl_realized_pnl_today_filters_by_kst_date(self) -> None:
        """금일(KST 자정 기준) 청산된 거래만 realized_pnl_today에 합산되고,
        어제 청산된 거래는 제외돼야 한다."""
        from datetime import UTC, datetime, timedelta

        from src.config import live_params as lp

        state = self._state()
        eng = self._mock_engine()

        today_kst_noon = datetime.now(lp.KST).replace(hour=12, minute=0, second=0, microsecond=0)
        yesterday_kst_noon = today_kst_noon - timedelta(days=1)

        trade_today = MagicMock()
        trade_today.pnl = 1234.0
        trade_today.exit_time = today_kst_noon.astimezone(UTC)

        trade_yesterday = MagicMock()
        trade_yesterday.pnl = 9999.0
        trade_yesterday.exit_time = yesterday_kst_noon.astimezone(UTC)

        eng.journal.trades = [trade_yesterday, trade_today]
        state.register_engine(eng)

        pnl = state.get_pnl()
        assert pnl["realized_pnl_today"] == pytest.approx(1234.0)

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

    def test_get_symbol_display_names_empty_without_engine(self) -> None:
        state = self._state()
        assert state.get_symbol_display_names() == {}

    def test_get_symbol_display_names_uses_korean_name(self) -> None:
        state = self._state()
        eng = self._mock_engine()
        ccxt_client = MagicMock()
        ccxt_client.markets = {
            "BTC/KRW": {"info": {"korean_name": "비트코인"}},
            "ETH/KRW": {"info": {"korean_name": "이더리움"}},
        }
        eng._exchange._exchange = ccxt_client  # noqa: SLF001
        state.register_engine(eng)

        names = state.get_symbol_display_names()
        assert names == {"BTC/KRW": "비트코인 (BTC)", "ETH/KRW": "이더리움 (ETH)"}

    def test_get_symbol_display_names_falls_back_without_korean_name(self) -> None:
        state = self._state()
        eng = self._mock_engine()
        ccxt_client = MagicMock()
        ccxt_client.markets = {
            "XRP/KRW": {"info": {}},
            "DOGE/KRW": {},
        }
        eng._exchange._exchange = ccxt_client  # noqa: SLF001
        state.register_engine(eng)

        names = state.get_symbol_display_names()
        assert names == {"XRP/KRW": "XRP", "DOGE/KRW": "DOGE"}

    def test_get_symbol_display_names_loads_markets_when_not_cached(self) -> None:
        state = self._state()
        eng = self._mock_engine()
        ccxt_client = MagicMock()
        ccxt_client.markets = {}

        def _load_markets():
            ccxt_client.markets = {"BTC/KRW": {"info": {"korean_name": "비트코인"}}}

        ccxt_client.load_markets.side_effect = _load_markets
        eng._exchange._exchange = ccxt_client  # noqa: SLF001
        state.register_engine(eng)

        names = state.get_symbol_display_names()
        ccxt_client.load_markets.assert_called_once()
        assert names == {"BTC/KRW": "비트코인 (BTC)"}

    def test_get_symbol_display_names_ignores_non_krw_markets(self) -> None:
        state = self._state()
        eng = self._mock_engine()
        ccxt_client = MagicMock()
        ccxt_client.markets = {
            "BTC/KRW": {"info": {"korean_name": "비트코인"}},
            "BTC/USDT": {"info": {"korean_name": "비트코인"}},
        }
        eng._exchange._exchange = ccxt_client  # noqa: SLF001
        state.register_engine(eng)

        names = state.get_symbol_display_names()
        assert names == {"BTC/KRW": "비트코인 (BTC)"}

    def test_get_symbol_display_names_returns_empty_on_exchange_error(self) -> None:
        state = self._state()
        eng = self._mock_engine()
        ccxt_client = MagicMock()
        ccxt_client.markets = {}
        ccxt_client.load_markets.side_effect = RuntimeError("boom")
        eng._exchange._exchange = ccxt_client  # noqa: SLF001
        state.register_engine(eng)

        assert state.get_symbol_display_names() == {}

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
        assert "₩9,500" in html
        assert "-₩500" in html
        # 일시정지/재개 버튼은 최초 로드(SSR)에도 있어야 한다 — JS refresh() 전까지
        # 버튼이 아예 없다가 나타나는 깜빡임(SSR/CSR 마크업 불일치)을 막는 회귀 테스트
        assert 'class="pnl-grid"' in html
        assert "일시정지" in html
        assert "재개" in html

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
        """value는 필터링용 원본 심볼("ETH/KRW")을 유지하고, 라벨만 symbol_names
        매핑이 없을 때의 폴백 표기("ETH")로 보여야 한다."""
        html = render_dashboard(
            self._status(), [], {}, trades=[self._trade(symbol="ETH/KRW")]
        )
        assert '<option value="ETH/KRW">ETH</option>' in html

    def test_html_trade_filter_symbol_option_uses_korean_display_name(self) -> None:
        """symbol_names 매핑이 있으면 "한글명 (티커)" 라벨로 표시하되 value는 원본
        심볼을 유지해야 한다 — 필터링 로직은 value에 의존한다."""
        html = render_dashboard(
            self._status(), [], {}, trades=[self._trade(symbol="BTC/KRW")],
            symbol_names={"BTC/KRW": "비트코인 (BTC)"},
        )
        assert '<option value="BTC/KRW">비트코인 (BTC)</option>' in html

    def test_html_trade_filter_period_defaults_to_today(self) -> None:
        html = render_dashboard(self._status(), [], {}, trades=[self._trade()])
        assert '<option value="today" selected>오늘</option>' in html

    def test_html_filter_trades_today_uses_kst_calendar_day(self) -> None:
        """'오늘' 필터는 롤링 24시간이 아니라 KST 달력 기준 당일 00:00부터여야 한다."""
        html = render_dashboard(self._status(), [], {}, trades=[self._trade()])
        assert "period === 'today'" in html
        assert "9 * 3600 * 1000" in html

    def test_html_position_symbol_uses_korean_display_name(self) -> None:
        positions = [{
            "symbol": "BTC/KRW",
            "side": "buy",
            "amount": 0.01,
            "entry_price": 50000.0,
            "stop_loss": 48000.0,
            "take_profit": None,
        }]
        html = render_dashboard(
            self._status(), positions, {}, symbol_names={"BTC/KRW": "비트코인 (BTC)"},
        )
        assert "<code>비트코인 (BTC)</code>" in html

    def test_html_position_symbol_falls_back_to_bare_ticker_without_mapping(self) -> None:
        positions = [{
            "symbol": "XRP/KRW",
            "side": "buy",
            "amount": 0.01,
            "entry_price": 500.0,
            "stop_loss": None,
            "take_profit": None,
        }]
        html = render_dashboard(self._status(), positions, {}, symbol_names={})
        assert "<code>XRP</code>" in html

    def test_html_sell_history_symbol_uses_korean_display_name(self) -> None:
        html = render_dashboard(
            self._status(), [], {}, trades=[self._trade(symbol="BTC/KRW")],
            symbol_names={"BTC/KRW": "비트코인 (BTC)"},
        )
        assert "<code>비트코인 (BTC)</code>" in html

    def test_html_buy_history_symbol_uses_korean_display_name(self) -> None:
        positions = [{
            "symbol": "BTC/KRW",
            "side": "buy",
            "amount": 0.01,
            "entry_price": 50_000_000.0,
            "entry_time": "2026-10-15T09:00:00",
        }]
        html = render_dashboard(
            self._status(), positions, {}, symbol_names={"BTC/KRW": "비트코인 (BTC)"},
        )
        assert "<code>비트코인 (BTC)</code>" in html

    def test_html_tick_panel_symbol_uses_korean_display_name(self) -> None:
        html = render_dashboard(
            self._status(), [], {}, ticks=[{"symbol": "BTC/KRW"}],
            symbol_names={"BTC/KRW": "비트코인 (BTC)"},
        )
        assert "<code>비트코인 (BTC)</code>" in html

    def test_html_tick_panel_symbol_falls_back_to_bare_ticker_without_mapping(self) -> None:
        html = render_dashboard(self._status(), [], {}, ticks=[{"symbol": "XRP/KRW"}], symbol_names={})
        assert "<code>XRP</code>" in html

    def test_html_tick_panel_js_uses_sym_label_helper(self) -> None:
        """클라이언트 JS renderTicks()도 symLabel()을 거쳐야 한다(서버/클라 일관성)."""
        html = render_dashboard(self._status(), [], {})
        assert "<code>${symLabel(t.symbol)}</code>" in html

    def test_html_sym_label_js_helper_present(self) -> None:
        """클라이언트 JS 측 심볼 라벨 헬퍼(symLabel)와 캐시 변수가 있어야 한다."""
        html = render_dashboard(self._status(), [], {})
        assert "function symLabel(sym)" in html
        assert "_symbolNames" in html
        assert "/api/symbol-names" in html

    def test_html_symbol_pnl_section_removed_from_dashboard(self) -> None:
        """'심볼별 손익 비교' 카드는 대시보드에서 제거됐다 — 데이터 재사용을 위해
        _render_symbol_pnl_svg()와 클라이언트 renderSymbolPnl() 함수 자체는 죽은
        코드로 남겨두되(별도로 직접 테스트됨), 페이지에는 더 이상 노출되지 않는다."""
        trades = [self._trade(symbol="BTC/KRW", pnl=100_000.0), self._trade(symbol="ETH/KRW", pnl=-50_000.0)]
        html = render_dashboard(self._status(), [], {}, trades=trades)
        assert "심볼별 손익 비교" not in html
        assert 'id="symbol-pnl-wrap"' not in html
        # 호출부만 제거되고 함수 정의 자체는 재사용을 위해 남아있어야 한다
        assert "function renderSymbolPnl(trades)" in html
        assert "renderSymbolPnl(_lastTrades)" not in html

    def test_html_theme_toggle_present(self) -> None:
        html = render_dashboard(self._status(), [], {})
        assert 'id="theme-toggle"' in html
        assert 'onclick="toggleTheme()"' in html
        assert "function toggleTheme()" in html
        assert "data-theme" in html
        assert "dashboard-theme" in html  # localStorage key

    # ── 라이트/다크 팔레트 회귀 테스트 ──────────────────────────────────────
    # 배경/카드/시그널/차트 색까지 전면 CSS 변수로 이관하기 전에는, 다크 테마를
    # 전제로 하드코딩된 텍스트색(#f1f5f9, #94a3b8, #64748b, #475569, #cbd5e1 등)이
    # 라이트 테마의 밝은 배경 위에서 대비가 사라져 텍스트가 보이지 않는 버그가 있었다.
    # 아래 테스트들은 그 하드코딩이 다시 섞여 들어오는 것을 막는 회귀 가드다.

    def test_html_no_hardcoded_dark_only_text_colors(self) -> None:
        html = render_dashboard(
            self._status(),
            positions=[{
                "symbol": "BTC/USDT", "side": "buy", "amount": 0.01,
                "entry_price": 50000.0, "stop_loss": 48000.0, "take_profit": None,
            }],
            pnl={"cash": 9500.0, "realized_pnl": -500.0, "unrealized_pnl": 100.0},
            trades=[self._trade()],
            stats={"total_trades": 10, "win_rate_pct": 60.0, "profit_factor": 1.5,
                   "avg_win": 100.0, "avg_loss": -50.0, "total_pnl": 500.0},
            balance=[{"currency": "BTC", "free": 0.01, "used": 0.0, "price": 50000.0,
                      "avg_buy_price": 48000.0, "buy_amount": 480.0, "eval_amount": 500.0}],
        )
        # 다크 배경을 전제로 한 하드코딩 텍스트/배경색 — 전부 var(--...)로 이관되어야 한다
        hardcoded_dark_colors = [
            "color:#f1f5f9", "color:#94a3b8", "color:#64748b", "color:#475569",
            "color:#cbd5e1", "color:#60a5fa", "color:#a78bfa", "color:#38bdf8",
            "color:#334155", "color:#3d5060", "color:#34d399", "color:#f87171",
            "background:#0f172a", "background:#10b98120", "background:#ef444425",
        ]
        for pattern in hardcoded_dark_colors:
            assert pattern not in html, f"하드코딩 다크 전제 색상이 재발함: {pattern}"

    def test_html_theme_variables_include_signal_and_chart_tokens(self) -> None:
        html = render_dashboard(self._status(), [], {})
        for token in (
            "--signal-buy", "--signal-buy-bg", "--signal-sell", "--signal-sell-bg",
            "--signal-warn", "--signal-warn-bg", "--accent", "--accent-bg",
            "--chart-bg", "--chart-grid", "--chart-grid-soft", "--chart-highlight",
        ):
            assert token in html

    def test_html_criteria_row_uses_theme_variable_not_hardcoded_hex(self) -> None:
        html = render_dashboard(self._status(), [], {})
        assert ".criteria-row{" in html
        assert "color:var(--text)" in html

    def test_html_buttons_use_signal_variables_not_raw_hex(self) -> None:
        html = render_dashboard(self._status(), [], {})
        assert ".btn-pause{" in html
        assert "background:#f59e0b" not in html
        assert "background:#10b981" not in html
        assert "background:#6366f1" not in html
        assert "var(--signal-warn)" in html
        assert "var(--signal-buy)" in html

    def test_html_balance_card_neutral_state_uses_theme_variable(self) -> None:
        """잔고 카드 '정보 없음'(손익 계산 불가) 상태가 다크 전제 고정 배경(#0f172a) 대신
        var(--bg-inset)를 쓰는지 확인한다 — 라이트 테마에서 카드 하나만 검게 떠 보이던 버그."""
        html = render_dashboard(
            self._status(), [], {},
            balance=[{"currency": "KRW", "free": 500000.0, "used": 0.0, "price": 1.0,
                      "avg_buy_price": 0.0, "buy_amount": 0, "eval_amount": 500000}],
        )
        assert "KRW" in html
        assert "background:#0f172a" not in html

    # ── 이모지 → 절제된 라인 아이콘 교체 회귀 테스트 ───────────────────────
    def test_html_replaces_target_emojis_with_svg_icons(self) -> None:
        html = render_dashboard(self._status(), [], {})
        for removed_emoji in ("⚡", "📋", "🔒", "🌙", "☀", "✅", "❌"):
            assert removed_emoji not in html, f"교체 대상 이모지가 남아있음: {removed_emoji}"
        # 대체된 아이콘은 stroke=currentColor 기반 인라인 SVG여야 한다(외부 아이콘 폰트 금지)
        assert 'stroke="currentColor"' in html

    def test_html_retains_out_of_scope_unicode_glyphs(self) -> None:
        """⏸▶✖ 은 이번 교체 범위 밖(이미 절제된 기호)이라 그대로 유지되어야 한다."""
        html = render_dashboard(self._status(), [], {})
        assert "⏸" in html
        assert "▶" in html

    # ── 전략 파라미터 & 신호 기준 패널 재설계 회귀 테스트 ──────────────────
    def test_html_strategy_panel_uses_unified_badge_vocabulary(self) -> None:
        """매수/매도 조건 배지 라벨 체계를 통일한다 — 매도 쪽도 매수와 같은 지표
        약어(EMA/MACD)를 쓰고 방향만 ↓ 접미사로 구분한다(구 '데스크로스' 라벨 제거)."""
        html = render_dashboard(self._status(), [], {})
        assert "function renderStrategy(s)" in html
        assert "'EMA↓'" in html
        assert "'MACD↓'" in html
        assert "sellPillLabels" not in html  # 구버전 별도 라벨 배열은 제거됨

    def test_html_strategy_panel_criteria_row_is_grid_layout(self) -> None:
        """배지(고정폭) | 조건식·설명 2행 구조의 2열 그리드인지 확인한다."""
        html = render_dashboard(self._status(), [], {})
        assert "grid-template-columns:58px 1fr" in html

    def test_html_strategy_panel_badge_has_fixed_width(self) -> None:
        """EMA/PROX/MACD 등 배지가 글자 수와 무관하게 고정 폭(.criteria-row .cp)을
        갖는지 확인한다 — 이전엔 justify-self:start 때문에 배지 폭이 제각각이었다."""
        html = render_dashboard(self._status(), [], {})
        assert ".criteria-row .cp{width:58px" in html

    def test_html_strategy_panel_condition_and_desc_are_stacked_lines(self) -> None:
        """조건식(<code>)과 설명이 같은 줄에서 경합하지 않고, 항상 조건식(줄1) /
        설명(줄2) 두 줄 구조로 분리되는지 확인한다(긴 설명 텍스트 포함)."""
        html = render_dashboard(self._status(), [], {})
        assert 'class="cond-line"' in html
        assert '<div class="cond-line"><code>' in html
        # 설명이 <code>와 같은 인라인 span이 아니라 별도 block div여야 줄이 분리된다
        assert '.criteria-row .desc{display:block' in html

    # ── SSR/CSR 마크업 동기화 회귀 테스트 ──────────────────────────────────
    # Python _render_*()(최초 로드)와 JS render*()(refresh())가 서로 다른 마크업을
    # 내면, 페이지를 열자마자 구 레이아웃이 잠깐 보였다가 refresh() 시점에 새
    # 레이아웃으로 바뀌는 깜빡임이 생긴다. 아래 테스트들은 SSR 결과물이 CSR과
    # 동일한 구조(클래스명)를 쓰는지 고정해 재발을 막는다.

    def test_html_status_card_matches_js_structure(self) -> None:
        """엔진 상태 카드는 배지(.badge) 목록이 아니라 renderStatus()(JS)와 동일한
        .status-box 2열 그리드 + 서킷 브레이커 행 구조여야 한다."""
        html = render_dashboard(self._status(), [], {})
        assert 'class="status-box"' in html
        assert html.count('class="status-box"') >= 2  # 모드 박스 + 상태 박스
        assert "서킷 브레이커" in html

    def test_html_stats_card_matches_js_structure(self) -> None:
        """성과 분석 카드는 renderStats()(JS)와 동일하게 .stats-flex(큰 승률) +
        .stat-box-grid(4박스) 구조여야 한다 — 구 .stats-grid(5박스 한 줄)는 제거됨."""
        html = render_dashboard(
            self._status(), [], {},
            stats={"total_trades": 10, "win_rate_pct": 60.0, "profit_factor": 1.5,
                   "avg_win": 100.0, "avg_loss": -50.0, "total_pnl": 500.0},
        )
        assert 'class="stats-flex"' in html
        assert 'class="stat-box-grid"' in html
        assert "stats-grid" not in html  # 클래스 자체가 제거됨(더 이상 아무도 안 씀)
        assert 'class="wr-bar"' in html

    def test_html_stats_card_empty_state_when_zero_trades(self) -> None:
        """거래가 0건이면 JS(renderStats)처럼 빈 상태 메시지를 보여야 한다 —
        기존엔 Python 쪽이 total_trades==0 체크를 안 해 0%/0.00 박스가 그려졌다."""
        html = render_dashboard(self._status(), [], {}, stats={"total_trades": 0})
        assert "거래 없음" in html

    def test_html_pnl_card_matches_js_structure(self) -> None:
        """포트폴리오 카드는 renderPnl()(JS)과 동일하게 .pnl-grid 6박스 +
        일시정지/재개 버튼을 최초 로드(SSR)부터 포함해야 한다."""
        html = render_dashboard(
            self._status(), [], {"cash": 9500.0, "realized_pnl": -500.0, "unrealized_pnl": 100.0},
        )
        assert 'class="pnl-grid"' in html
        assert 'class="btn-pause"' in html
        assert 'class="btn-resume"' in html

    def test_html_portfolio_card_shows_six_expanded_boxes(self) -> None:
        """포트폴리오 카드가 신규 필드(코인 보유 잔고/총 합산 잔고/금일 실현
        손익/누적 수익률)까지 6박스로 확장되어 노출되는지 확인한다."""
        pnl = {
            "cash": 1_000_000.0,
            "realized_pnl": 50_000.0,
            "realized_pnl_today": 20_000.0,
            "unrealized_pnl": 5_000.0,
            "open_positions": 1,
            "coin_value": 300_000.0,
            "total_balance": 1_300_000.0,
            "initial_cash": 1_000_000.0,
            "total_return": 300_000.0,
            "total_return_pct": 30.0,
        }
        html = render_dashboard(self._status(), [], pnl)
        assert "코인 보유 잔고" in html
        assert "총 합산 잔고" in html
        assert "금일 실현 손익" in html
        assert "미실현 손익" in html
        assert "누적 수익률" in html
        assert "₩1,000,000" in html  # 현금 잔고
        assert "₩300,000" in html  # 코인 보유 잔고
        assert "₩1,300,000" in html  # 총 합산 잔고
        assert "+₩20,000" in html  # 금일 실현 손익
        assert "누적 +₩50,000" in html  # 보조 텍스트(전체 누적 실현 손익)
        assert "+30.0%" in html  # 누적 수익률 %

    def test_html_portfolio_card_defaults_missing_new_fields_to_zero(self) -> None:
        """새 필드가 없는 pnl dict(예: 구 테스트/다른 호출부)가 와도 KeyError 없이
        0으로 폴백해야 한다."""
        html = render_dashboard(
            self._status(), [], {"cash": 9500.0, "realized_pnl": -500.0, "unrealized_pnl": 100.0},
        )
        assert "₩9,500" in html
        assert "-₩500" in html  # 누적 실현손익 보조 텍스트에 그대로 남아있어야 함

    # ── 탭 순서 회귀 테스트 (매도이력 → 매수이력 → 오픈포지션) ─────────────

    def test_html_tabs_order_is_sell_buy_positions(self) -> None:
        """탭 버튼 순서가 매도이력 → 매수이력 → 오픈포지션이어야 하고, 매도이력이
        기본 활성 탭이어야 한다(진입 즉시 매수이력에 뜨므로, 매도이력을 먼저 본다)."""
        html = render_dashboard(self._status(), [], {})
        idx_sell_btn = html.index("onclick=\"switchTab('sells',this)\"")
        idx_buy_btn = html.index("onclick=\"switchTab('buys',this)\"")
        idx_pos_btn = html.index("onclick=\"switchTab('positions',this)\"")
        assert idx_sell_btn < idx_buy_btn < idx_pos_btn
        assert '<button class="tab active" onclick="switchTab(\'sells\',this)"' in html
        assert '<div id="tab-sells" class="tab-panel active">' in html

    def test_html_tabs_index_based_js_references_match_new_order(self) -> None:
        """mainTabs[0]/[1]/[2] 인덱스 참조가 새 DOM 순서(매도/매수/포지션)와
        일치하는지 확인한다 — 탭 이름과 실제 갱신 대상이 어긋나는 회귀 방지."""
        html = render_dashboard(self._status(), [], {})
        assert "if (mainTabs[0]) mainTabs[0].textContent = `매도 이력" in html
        assert "if (mainTabs[1]) mainTabs[1].textContent = `매수 이력" in html
        assert "if (mainTabs[2]) mainTabs[2].textContent = `오픈 포지션" in html

    # ── 차트 회귀 테스트 (실거래 규모: 30일치 일별 손익 / 8개 심볼) ─────────
    # 더미 2~3개 막대로만 검증해서 실거래 규모에서 라벨이 겹치는 문제를
    # 놓쳤던 사고의 재발 방지 — 반드시 실제 규모(한 달 전체 일수, 8개 심볼)로
    # 데이터를 만들어 검증한다. 업비트 데이터랩 참고 이후: "겹치면 전부 생략" 대신
    # "겹치는 라벨만 개별적으로 생략"하는 그리디 배치로 바뀌었으므로, 라벨이 아예
    # 없는지가 아니라 "겹치지 않는지 + 최대한 많이 보이는지"를 검증한다.

    # 막대 위 값 레이블에만 쓰이는 스타일 조합(축 눈금/총합/날짜·심볼 라벨과는 font-size
    # + text-anchor + font-weight 조합이 겹치지 않는다) — 이 시그니처로 라벨 x좌표만
    # 뽑아서 겹침 여부를 기하학적으로 확인한다.
    _EQUITY_BAR_LABEL_RE = (
        r'<text x="([\d.]+)" y="[\d.]+" fill="var\(--chart-(?:buy|sell)\)" '
        r'font-size="10" text-anchor="middle" font-weight="600"'
    )
    _SYMBOL_BAR_LABEL_RE = (
        r'<text x="([\d.]+)" y="[\d.]+" fill="var\(--chart-(?:buy|sell)\)" '
        r'font-size="10" text-anchor="middle" font-weight="700"'
    )

    def test_html_equity_chart_full_month_labels_dont_overlap_but_mostly_show(self) -> None:
        """실거래 규모(이번 달 전체 일수)로 막대가 촘촘해져도, 그리디 배치 덕분에
        라벨끼리 겹치지 않으면서(최소 간격 보장) 절반 이상은 실제로 표시돼야
        한다 — 이전엔 조금이라도 겹칠 것 같으면 전부 생략해 버렸다."""
        import calendar
        import re
        from datetime import date

        today = date.today()
        last_day = calendar.monthrange(today.year, today.month)[1]
        equity = [
            {
                "time": f"{today.year:04d}-{today.month:02d}-{d:02d}T00:00:00",
                "pnl": 4227.0 if d % 2 == 0 else -5262.0,
            }
            for d in range(1, last_day + 1)
        ]
        svg = _render_equity_svg(equity)
        xs = [float(m) for m in re.findall(self._EQUITY_BAR_LABEL_RE, svg)]
        assert len(xs) >= last_day // 3, "라벨이 너무 적게 표시됨(과도한 생략)"
        for a, b in zip(xs, xs[1:]):
            assert b - a >= 10, "라벨끼리 겹치거나 너무 붙어 보임"
        # 좌측 상단 합계(월 누적, 모바일에서 스크롤 없이 바로 보이도록 좌측 배치)는
        # 항상 정확한 자릿수로 보여야 한다
        assert 'text-anchor="start" font-weight="800"' in svg

    def test_html_equity_chart_total_label_anchored_top_left(self) -> None:
        """누적 합계 라벨이 우측상단이 아니라 월 제목과 같은 좌측 x좌표에 그려져야
        한다 — 모바일 좁은 화면(가로 스크롤 chart-wrap)에서 스크롤 없이 바로
        보이게 하려는 변경."""
        import re
        from datetime import date

        today = date.today()
        equity = [{"time": f"{today.isoformat()}T00:00:00", "pnl": 1000.0}]
        svg = _render_equity_svg(equity)
        title_x = re.search(r'<text x="([\d.]+)" y="20" fill="var\(--text-muted\)"', svg).group(1)
        total_x = re.search(r'<text x="([\d.]+)" y="[\d.]+" fill="var\(--chart-buy\)" font-size="22"', svg).group(1)
        assert title_x == total_x, "합계 라벨이 월 제목과 같은 좌측 x좌표에 있어야 한다"

    def test_html_equity_chart_axis_and_total_keep_full_precision(self) -> None:
        """축 눈금·합계처럼 개수가 적은 곳은 만/억 축약 없이 정확한 전체 자릿수를
        유지해야 한다(축약은 막대 위 값 라벨에만 적용)."""
        from datetime import date

        today = date.today()
        equity = [{"time": f"{today.isoformat()}T00:00:00", "pnl": 123456.0}]
        svg = _render_equity_svg(equity)
        assert "123,456" in svg  # 축/합계 어딘가엔 콤마 표기 전체 자릿수가 나타남

    def test_html_symbol_pnl_chart_eight_symbols_labels_dont_overlap(self) -> None:
        """실거래 규모(8개 심볼)로도 그리디 배치 덕분에 라벨끼리 겹치지 않아야
        한다(더미 2개 심볼로만 검증해 겹침을 놓쳤던 문제의 회귀 테스트)."""
        import re

        trades = [
            self._trade(symbol=f"SYM{i}/KRW", pnl=(4227.0 if i % 2 == 0 else -5262.0))
            for i in range(8)
        ]
        svg = _render_symbol_pnl_svg(trades)
        xs = [float(m) for m in re.findall(self._SYMBOL_BAR_LABEL_RE, svg)]
        for a, b in zip(xs, xs[1:]):
            assert b - a >= 10, "라벨끼리 겹치거나 너무 붙어 보임"
        # 심볼 티커(x축) 라벨은 값 라벨 개수와 무관하게 8개 전부 보여야 한다
        assert "SYM0" in svg and "SYM7" in svg

    def test_html_symbol_pnl_chart_few_symbols_shows_bar_label(self) -> None:
        """심볼 수가 적으면(소규모) 막대별 값 라벨이 정상적으로 보여야 한다 —
        큰 금액은 만/억 단위로 축약 표기된다(예: 100,000원 → 10만원)."""
        import re

        trades = [self._trade(symbol="BTC/KRW", pnl=100_000.0)]
        svg = _render_symbol_pnl_svg(trades)
        assert re.search(self._SYMBOL_BAR_LABEL_RE, svg)
        assert "+₩10만" in svg

    def test_html_chart_bars_use_dedicated_vivid_chart_tokens(self) -> None:
        """차트 막대는 절제된 구조색(--signal-buy/--signal-sell)이 아니라 어두운
        배경 위에서 도드라지도록 채도를 높인 전용 토큰(--chart-buy/--chart-sell)을
        써야 한다 — 구조적 UI(버튼/배지) 톤은 그대로 두고 데이터 시각화만 조정."""
        equity = [{"time": "2026-09-01T00:00:00", "pnl": 100.0}]
        html = render_dashboard(self._status(), [], {}, equity=equity)
        assert "--chart-buy" in html
        assert "--chart-sell" in html
        assert 'fill="var(--chart-buy)"' in html

    def test_html_chart_bar_width_has_min_and_max_clamp(self) -> None:
        """막대 폭 계산식(barW)이 슬롯 폭의 고정 비율(약 55%)에 상한/하한 클램프를
        두는지 확인한다 — 라벨이 더 많이 뜨도록 이전(62%)보다 간격을 넓혔다."""
        html = render_dashboard(self._status(), [], {})
        assert "slot * 0.55" in html  # renderEquity (JS)
        assert "symSlot * 0.55" in html  # renderSymbolPnl (JS)

    def test_html_chart_grid_uses_faint_translucent_tokens(self) -> None:
        """그리드라인이 업비트 데이터랩처럼 '거의 안 보일 정도'로 옅어야 한다 —
        불투명 회색 hex 대신 저알파 rgba 토큰을 쓴다."""
        html = render_dashboard(self._status(), [], {})
        assert "--chart-grid:rgba(" in html
        assert "--chart-grid-soft:rgba(" in html


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

    def test_get_symbol_names_returns_dict(self, client) -> None:
        resp = client.get("/api/symbol-names")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

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
