"""Unit tests for DailyReportGenerator."""
from __future__ import annotations

from datetime import UTC, datetime, date, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from src.report.generator import DailyReportGenerator
from src.portfolio.journal import TradeRecord
from src.portfolio.journal_store import SQLiteJournalStore

KST = timezone(timedelta(hours=9))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_store(db_path: Path) -> SQLiteJournalStore:
    return SQLiteJournalStore(db_path=db_path)


def _save_trade(store: SQLiteJournalStore, pnl: float, reason: str = "signal",
                exit_offset_hours: int = 0, symbol: str = "BTC/KRW") -> None:
    store.save(TradeRecord(
        symbol=symbol,
        side="buy",
        amount=Decimal("0.01"),
        entry_price=Decimal("50000"),
        exit_price=Decimal("51000"),
        entry_time=datetime(2026, 4, 7, 18, tzinfo=UTC),
        exit_time=datetime(2026, 4, 7, 19 + exit_offset_hours, tzinfo=UTC),
        pnl=Decimal(str(pnl)),
        commission=Decimal("0"),
        reason=reason,
    ))


@pytest.fixture()
def gen_env(tmp_path, monkeypatch):
    import src.report.generator as gen_mod
    monkeypatch.setattr(gen_mod, 'REPORTS_DIR', tmp_path)
    _make_store(tmp_path / "j.db")
    return tmp_path


# ── Init ──────────────────────────────────────────────────────────────────────

class TestDailyReportGeneratorInit:
    def test_default_symbols(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        assert len(gen._symbols) >= 1

    def test_custom_symbols(self, tmp_path):
        gen = DailyReportGenerator(
            journal_path=str(tmp_path / "j.db"),
            symbols=["BTC/KRW", "ETH/KRW"],
        )
        assert gen._symbols == ["BTC/KRW", "ETH/KRW"]

    def test_run_tasks_default_false(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        assert gen._run_tasks is False

    def test_run_tasks_can_be_set(self, tmp_path):
        gen = DailyReportGenerator(
            journal_path=str(tmp_path / "j.db"),
            run_tasks=True,
        )
        assert gen._run_tasks is True

    def test_last_task_results_empty_initially(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        assert gen.last_task_results == []


# ── generate() ────────────────────────────────────────────────────────────────

class TestDailyReportGeneratorGenerate:
    def test_generate_creates_markdown_file(self, gen_env):
        gen = DailyReportGenerator(journal_path=str(gen_env / "j.db"))
        with patch.object(gen, '_fetch_market_status', return_value=[]):
            path = gen.generate("2026-04-28")
        assert path.exists()
        assert path.suffix == ".md"

    def test_generate_file_contains_target_date(self, gen_env):
        gen = DailyReportGenerator(journal_path=str(gen_env / "j.db"))
        with patch.object(gen, '_fetch_market_status', return_value=[]):
            path = gen.generate("2026-04-28")
        content = path.read_text(encoding="utf-8")
        assert "2026-04-28" in content

    def test_generate_today_when_no_date(self, gen_env):
        gen = DailyReportGenerator(journal_path=str(gen_env / "j.db"))
        with patch.object(gen, '_fetch_market_status', return_value=[]):
            path = gen.generate()
        assert path.exists()

    def test_generate_with_specific_date(self, gen_env):
        gen = DailyReportGenerator(journal_path=str(gen_env / "j.db"))
        with patch.object(gen, '_fetch_market_status', return_value=[]):
            path = gen.generate("2026-01-15")
        assert "2026-01-15" in path.name

    def test_generate_contains_strategy_header(self, gen_env):
        gen = DailyReportGenerator(journal_path=str(gen_env / "j.db"))
        with patch.object(gen, '_fetch_market_status', return_value=[]):
            path = gen.generate("2026-04-28")
        content = path.read_text(encoding="utf-8")
        assert "RegimeAdaptiveStrategy" in content

    def test_generate_runs_tasks_when_enabled(self, gen_env, monkeypatch):
        import src.report.task_runner as tr
        monkeypatch.setattr(tr, 'REPORTS_DIR', gen_env)
        monkeypatch.setattr(tr, 'REC_DIR', gen_env / "rec")
        monkeypatch.setattr(tr, 'STATE_FILE', gen_env / ".state.json")
        (gen_env / "rec").mkdir(exist_ok=True)

        gen = DailyReportGenerator(
            journal_path=str(gen_env / "j.db"),
            run_tasks=True,
        )
        with patch.object(gen, '_fetch_market_status', return_value=[]):
            path = gen.generate("2026-04-28")
        assert path.exists()
        # last_task_results is a list (may be empty since 0 trades)
        assert isinstance(gen.last_task_results, list)


# ── Query methods ─────────────────────────────────────────────────────────────

class TestDailyReportGeneratorQueries:
    def test_query_strategy_stats_empty_db(self, tmp_path):
        db = tmp_path / "j.db"
        _make_store(db)
        gen = DailyReportGenerator(journal_path=str(db))
        stats = gen._query_strategy_stats(0)
        assert stats["total"] == 0
        assert stats["wins"] == 0
        assert stats["wr_pct"] == 0

    def test_query_strategy_stats_with_trades(self, tmp_path):
        db = tmp_path / "j.db"
        store = _make_store(db)
        _save_trade(store, pnl=100.0, exit_offset_hours=0)
        _save_trade(store, pnl=-50.0, exit_offset_hours=1)
        _save_trade(store, pnl=200.0, exit_offset_hours=2, reason="take_profit")

        gen = DailyReportGenerator(journal_path=str(db))
        stats = gen._query_strategy_stats(0)
        assert stats["total"] == 3
        assert stats["wins"] == 2
        assert abs(stats["wr_pct"] - 66.67) < 0.1

    def test_query_strategy_stats_profit_factor_inf_when_no_losses(self, tmp_path):
        db = tmp_path / "j.db"
        store = _make_store(db)
        _save_trade(store, pnl=100.0)
        gen = DailyReportGenerator(journal_path=str(db))
        stats = gen._query_strategy_stats(0)
        assert stats["profit_factor"] is None  # encoded as None for ∞

    def test_query_all_stats_empty(self, tmp_path):
        db = tmp_path / "j.db"
        _make_store(db)
        gen = DailyReportGenerator(journal_path=str(db))
        stats = gen._query_all_stats()
        assert stats["total"] == 0
        assert stats["total_pnl"] == 0

    def test_query_all_stats_with_trades(self, tmp_path):
        db = tmp_path / "j.db"
        store = _make_store(db)
        _save_trade(store, pnl=100.0)
        _save_trade(store, pnl=100.0, exit_offset_hours=1)
        _save_trade(store, pnl=-50.0, exit_offset_hours=2)
        gen = DailyReportGenerator(journal_path=str(db))
        stats = gen._query_all_stats()
        assert stats["total"] == 3
        assert stats["wins"] == 2

    def test_query_day_trades_empty(self, tmp_path):
        db = tmp_path / "j.db"
        _make_store(db)
        gen = DailyReportGenerator(journal_path=str(db))
        trades = gen._query_day_trades(date(2026, 4, 28))
        assert trades == []

    def test_fetch_market_status_returns_list(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        result = gen._fetch_market_status()
        assert isinstance(result, list)


# ── _pending_tasks() ──────────────────────────────────────────────────────────

class TestPendingTasks:
    def _empty_stats(self, total=0, wins=0, pnl=0.0, sl=0, tp=0, sig=0) -> dict:
        return {
            "total": total, "wins": wins, "losses": total - wins,
            "wr_pct": wins / max(total, 1) * 100,
            "total_pnl": pnl, "avg_pnl": 0,
            "profit_factor": None,
            "sl_count": sl, "tp_count": tp, "signal_count": sig,
        }

    def test_accumulating_data_task(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        tasks = gen._pending_tasks(self._empty_stats(total=5))
        assert any("20건" in t for t in tasks)

    def test_walk_forward_task_after_20(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        tasks = gen._pending_tasks(self._empty_stats(total=30))
        assert any("50건" in t for t in tasks)

    def test_low_wr_task(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        stats = self._empty_stats(total=15, wins=4)
        stats["wr_pct"] = 26.7
        tasks = gen._pending_tasks(stats)
        assert any("승률" in t for t in tasks)

    def test_negative_pnl_task(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        stats = self._empty_stats(total=15, wins=8, pnl=-500.0)
        stats["wr_pct"] = 53.3
        tasks = gen._pending_tasks(stats)
        assert any("PnL" in t for t in tasks)

    def test_high_sl_ratio_task(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        # sl_count (8) > sig_count (2) + tp_count (1) = 3
        stats = self._empty_stats(total=12, wins=5, sl=8, sig=2, tp=1)
        stats["wr_pct"] = 41.7
        tasks = gen._pending_tasks(stats)
        assert any("SL" in t for t in tasks)

    def test_always_includes_paper_mode_reminder(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        tasks = gen._pending_tasks(self._empty_stats())
        assert any("페이퍼" in t for t in tasks)


# ── _render() ─────────────────────────────────────────────────────────────────

class TestRender:
    def _base_stats(self) -> dict:
        return {
            "total": 0, "wins": 0, "losses": 0, "wr_pct": 0,
            "total_pnl": 0, "avg_pnl": 0, "profit_factor": None,
            "sl_count": 0, "tp_count": 0, "signal_count": 0,
        }

    def _all_stats(self) -> dict:
        return {"total": 0, "wins": 0, "losses": 0, "wr_pct": 0, "total_pnl": 0}

    def test_render_contains_date(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        md = gen._render(
            report_date=date(2026, 4, 28),
            market=[],
            prev_trades=[],
            today_trades=[],
            mr_stats=self._base_stats(),
            all_stats=self._all_stats(),
            tasks=[],
        )
        assert "2026-04-28" in md

    def test_render_contains_strategy_name(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        md = gen._render(
            report_date=date(2026, 4, 28),
            market=[],
            prev_trades=[],
            today_trades=[],
            mr_stats=self._base_stats(),
            all_stats=self._all_stats(),
            tasks=[],
        )
        assert "RegimeAdaptiveStrategy" in md

    def test_render_with_prev_trades(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        prev_trades = [{
            "symbol": "BTC/KRW",
            "entry_price": 50000.0,
            "exit_price": 51000.0,
            "pnl": 1000.0,
            "reason": "take_profit",
            "entry_kst": "2026-04-27 10:00:00",
            "exit_kst": "2026-04-27 11:00:00",
        }]
        md = gen._render(
            report_date=date(2026, 4, 28),
            market=[],
            prev_trades=prev_trades,
            today_trades=[],
            mr_stats=self._base_stats(),
            all_stats=self._all_stats(),
            tasks=[],
        )
        assert "BTC/KRW" in md
        assert "take_profit" in md

    def test_render_with_today_trades(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        today_trades = [{
            "symbol": "ETH/KRW",
            "entry_price": 3000.0,
            "exit_price": 3100.0,
            "pnl": 100.0,
            "reason": "signal",
            "entry_kst": None,
            "exit_kst": None,
        }]
        md = gen._render(
            report_date=date(2026, 4, 28),
            market=[],
            prev_trades=[],
            today_trades=today_trades,
            mr_stats=self._base_stats(),
            all_stats=self._all_stats(),
            tasks=[],
        )
        assert "ETH/KRW" in md

    def test_render_with_market_buy_signal(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        market = [
            {"symbol": "BTC/KRW", "price": 100000000.0,
             "rsi14": 18.0, "rsi5": 15.0, "vol_ratio": 3.0},
        ]
        md = gen._render(
            report_date=date(2026, 4, 28),
            market=market,
            prev_trades=[],
            today_trades=[],
            mr_stats=self._base_stats(),
            all_stats=self._all_stats(),
            tasks=[],
        )
        assert "BTC/KRW" in md
        assert "BUY" in md

    def test_render_with_market_sell_signal(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        market = [
            {"symbol": "ETH/KRW", "price": 3000000.0,
             "rsi14": 65.0, "rsi5": 70.0, "vol_ratio": 1.2},
        ]
        md = gen._render(
            report_date=date(2026, 4, 28),
            market=market,
            prev_trades=[],
            today_trades=[],
            mr_stats=self._base_stats(),
            all_stats=self._all_stats(),
            tasks=[],
        )
        assert "SELL" in md

    def test_render_with_market_hold_signal(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        market = [
            {"symbol": "XRP/KRW", "price": 1000.0,
             "rsi14": 45.0, "rsi5": 48.0, "vol_ratio": 0.8},
        ]
        md = gen._render(
            report_date=date(2026, 4, 28),
            market=market,
            prev_trades=[],
            today_trades=[],
            mr_stats=self._base_stats(),
            all_stats=self._all_stats(),
            tasks=[],
        )
        assert "HOLD" in md

    def test_render_with_missing_market_price(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        market = [
            {"symbol": "LINK/KRW", "price": None, "rsi14": None, "rsi5": None, "vol_ratio": None},
        ]
        md = gen._render(
            report_date=date(2026, 4, 28),
            market=market,
            prev_trades=[],
            today_trades=[],
            mr_stats=self._base_stats(),
            all_stats=self._all_stats(),
            tasks=[],
        )
        assert "LINK/KRW" in md

    def test_render_with_tasks(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        md = gen._render(
            report_date=date(2026, 4, 28),
            market=[],
            prev_trades=[],
            today_trades=[],
            mr_stats=self._base_stats(),
            all_stats=self._all_stats(),
            tasks=["파라미터 재검토", "Walk-Forward 실행"],
        )
        assert "파라미터 재검토" in md
        assert "Walk-Forward 실행" in md

    def test_render_with_task_results_executed(self, tmp_path):
        from src.report.task_runner import ActionResult
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        task_results = [
            ActionResult("t1", "Test Task", "executed", "detail", str(tmp_path / "file.json")),
        ]
        md = gen._render(
            report_date=date(2026, 4, 28),
            market=[],
            prev_trades=[],
            today_trades=[],
            mr_stats=self._base_stats(),
            all_stats=self._all_stats(),
            tasks=[],
            task_results=task_results,
        )
        assert "Test Task" in md
        assert "file.json" in md

    def test_render_with_task_results_error(self, tmp_path):
        from src.report.task_runner import ActionResult
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        task_results = [
            ActionResult("t2", "Error Task", "error", "some error", None),
        ]
        md = gen._render(
            report_date=date(2026, 4, 28),
            market=[],
            prev_trades=[],
            today_trades=[],
            mr_stats=self._base_stats(),
            all_stats=self._all_stats(),
            tasks=[],
            task_results=task_results,
        )
        assert "Error Task" in md

    def test_render_no_task_results_shows_no_tasks_message(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        md = gen._render(
            report_date=date(2026, 4, 28),
            market=[],
            prev_trades=[],
            today_trades=[],
            mr_stats=self._base_stats(),
            all_stats=self._all_stats(),
            tasks=[],
            task_results=None,
        )
        assert "조건 미충족" in md

    def test_render_profit_factor_infinity_displayed(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        mr_stats = self._base_stats()
        mr_stats.update({"total": 1, "wins": 1, "wr_pct": 100.0, "total_pnl": 500.0,
                          "avg_pnl": 500.0, "profit_factor": None})
        md = gen._render(
            report_date=date(2026, 4, 28),
            market=[],
            prev_trades=[],
            today_trades=[],
            mr_stats=mr_stats,
            all_stats=self._all_stats(),
            tasks=[],
        )
        assert "∞" in md

    def test_render_prev_trades_with_none_timestamps(self, tmp_path):
        gen = DailyReportGenerator(journal_path=str(tmp_path / "j.db"))
        prev_trades = [{
            "symbol": "BTC/KRW",
            "entry_price": None,
            "exit_price": None,
            "pnl": 100.0,
            "reason": "signal",
            "entry_kst": None,
            "exit_kst": None,
        }]
        md = gen._render(
            report_date=date(2026, 4, 28),
            market=[],
            prev_trades=prev_trades,
            today_trades=[],
            mr_stats=self._base_stats(),
            all_stats=self._all_stats(),
            tasks=[],
        )
        assert "BTC/KRW" in md
