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
        """포트폴리오 카드는 renderPnl()(JS)과 동일하게 .pnl-grid 3박스 +
        일시정지/재개 버튼을 최초 로드(SSR)부터 포함해야 한다."""
        html = render_dashboard(
            self._status(), [], {"cash": 9500.0, "realized_pnl": -500.0, "unrealized_pnl": 100.0},
        )
        assert 'class="pnl-grid"' in html
        assert 'class="btn-pause"' in html
        assert 'class="btn-resume"' in html

    # ── 차트 회귀 테스트 (실거래 규모: 30일치 일별 손익 / 8개 심볼) ─────────
    # 더미 2~3개 막대로만 검증해서 실거래 규모에서 라벨이 겹치는 문제를
    # 놓쳤던 사고의 재발 방지 — 반드시 실제 규모(한 달 전체 일수, 8개 심볼)로
    # 데이터를 만들어 검증한다.

    # 막대 위 값 레이블에만 쓰이는 스타일 조합(축 눈금/총합/날짜·심볼 라벨과는 font-size
    # + text-anchor + font-weight 조합이 겹치지 않는다) — 이 시그니처의 존재 여부로
    # "막대별 라벨이 생략됐는지"를 축 라벨과 혼동 없이 판별한다.
    _EQUITY_BAR_LABEL_STYLE = 'font-size="12" text-anchor="middle" font-weight="600"'
    _SYMBOL_BAR_LABEL_STYLE = 'font-size="12" text-anchor="middle" font-weight="700"'

    def test_html_equity_chart_full_month_suppresses_overlapping_bar_labels(self) -> None:
        """실거래 규모(이번 달 전체 일수)로 막대가 촘촘해지면, 막대 중심 간 거리가
        라벨 폭보다 좁아지므로 막대별 값 라벨은 생략되고 우측 상단 합계만 남아야
        한다(더미 2~3개 막대로만 검증해 겹침을 놓쳤던 문제의 회귀 테스트)."""
        import calendar
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
        assert self._EQUITY_BAR_LABEL_STYLE not in svg
        # 우측 상단 합계(월 누적)는 계속 보여야 한다
        assert "text-anchor=\"end\" font-weight=\"800\"" in svg

    def test_html_equity_chart_always_spans_full_month(self) -> None:
        """equity 차트는 거래가 하루치만 있어도 이번 달 전체 일수(28~31개) 막대를
        그린다 — 즉 '막대 수가 적은 경우'가 구조적으로 존재하지 않으므로, 라벨
        생략 로직이 항상 이 규모를 전제로 동작하는지(과도하게 숨기지 않는지)
        아주 짧은 라벨(한 자릿수 원)로 확인한다."""
        from datetime import date

        today = date.today()
        equity = [{"time": f"{today.isoformat()}T00:00:00", "pnl": 5.0}]
        svg = _render_equity_svg(equity)
        assert "+₩5" in svg  # 축 눈금/합계 어딘가엔 반드시 나타남

    def test_html_symbol_pnl_chart_eight_symbols_suppresses_overlapping_labels(self) -> None:
        """실거래 규모(8개 심볼)로 막대가 촘촘해지면 값 라벨 겹침을 피하기 위해
        막대별 라벨을 생략해야 한다(더미 2개 심볼로만 검증해 놓쳤던 문제의 회귀 테스트)."""
        trades = [
            self._trade(symbol=f"SYM{i}/KRW", pnl=(4227.0 if i % 2 == 0 else -5262.0))
            for i in range(8)
        ]
        svg = _render_symbol_pnl_svg(trades)
        assert self._SYMBOL_BAR_LABEL_STYLE not in svg
        # 심볼 티커(x축) 라벨은 값 라벨 생략과 무관하게 계속 보여야 한다
        assert "SYM0" in svg

    def test_html_symbol_pnl_chart_few_symbols_still_shows_bar_labels(self) -> None:
        """심볼 수가 적으면(소규모) 기존처럼 막대별 값 라벨이 정상적으로 보여야
        한다 — 겹침 방지 로직이 과도하게 라벨을 숨기지 않는지 확인."""
        trades = [self._trade(symbol="BTC/KRW", pnl=100_000.0)]
        svg = _render_symbol_pnl_svg(trades)
        assert self._SYMBOL_BAR_LABEL_STYLE in svg
        assert "+₩100,000" in svg

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
        """막대 폭 계산식(barW)이 슬롯 폭의 고정 비율(약 62%)에 상한/하한 클램프를
        두는지 확인한다 — 이전엔 상한이 없어 막대 수가 적을 때 과도하게 두꺼워졌다."""
        html = render_dashboard(self._status(), [], {})
        assert "slot * 0.62" in html  # renderEquity (JS)
        assert "symSlot * 0.62" in html  # renderSymbolPnl (JS)


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
