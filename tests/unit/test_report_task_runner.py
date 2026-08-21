"""Unit tests for ScheduledTaskRunner and ActionResult."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.report.task_runner import ActionResult, ScheduledTaskRunner
from src.portfolio.journal import TradeRecord
from src.portfolio.journal_store import SQLiteJournalStore


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_trades(db_path: Path, count: int, pnl: float = 100.0,
                 reason: str = "signal") -> None:
    store = SQLiteJournalStore(db_path=db_path)
    for i in range(count):
        store.save(TradeRecord(
            symbol="BTC/KRW",
            side="buy",
            amount=Decimal("0.01"),
            entry_price=Decimal("50000"),
            exit_price=Decimal("51000"),
            entry_time=datetime(2026, 4, 1, tzinfo=UTC),
            exit_time=datetime(2026, 4, 1, 1, i, tzinfo=UTC),
            pnl=Decimal(str(pnl)),
            commission=Decimal("0"),
            reason=reason,
        ))


def _mr_stats(total=0, wins=0, pnl=0.0, sl=0, tp=0, sig=0) -> dict:
    return {
        "total":         total,
        "wins":          wins,
        "losses":        total - wins,
        "wr_pct":        wins / max(total, 1) * 100,
        "total_pnl":     pnl,
        "avg_pnl":       pnl / max(total, 1),
        "profit_factor": None,
        "sl_count":      sl,
        "tp_count":      tp,
        "signal_count":  sig,
    }


@pytest.fixture()
def runner_env(tmp_path, monkeypatch):
    """Set up patched report dirs and return (runner_factory, tmp_path).

    Also redirects src.config.live_params' file paths into tmp_path so tests
    never read/write the real .strategy_params.json / .restart_requested that
    the live watchdog uses.
    """
    import src.report.task_runner as tr
    import src.config.live_params as lp
    monkeypatch.setattr(tr, 'REPORTS_DIR', tmp_path)
    monkeypatch.setattr(tr, 'REC_DIR', tmp_path / "rec")
    monkeypatch.setattr(tr, 'STATE_FILE', tmp_path / ".task_state.json")
    monkeypatch.setattr(lp, 'PARAMS_FILE', tmp_path / ".strategy_params.json")
    monkeypatch.setattr(lp, 'AUDIT_LOG', tmp_path / "param_change_log.jsonl")
    monkeypatch.setattr(lp, 'RESTART_FLAG', tmp_path / ".restart_requested")
    (tmp_path / "rec").mkdir()
    return tmp_path


# ── ActionResult ──────────────────────────────────────────────────────────────

class TestActionResult:
    def test_to_telegram_executed_has_checkmark(self):
        r = ActionResult("id1", "Title", "executed", "detail", "/path/file.json")
        msg = r.to_telegram()
        assert "✅" in msg
        assert "Title" in msg

    def test_to_telegram_executed_includes_filename(self):
        r = ActionResult("id1", "Title", "executed", "detail", "/path/result.json")
        msg = r.to_telegram()
        assert "result.json" in msg

    def test_to_telegram_error_has_warning(self):
        r = ActionResult("id2", "Error Task", "error", "something failed", None)
        msg = r.to_telegram()
        assert "⚠️" in msg
        assert "Error Task" in msg

    def test_to_telegram_skipped_has_skip_icon(self):
        r = ActionResult("id3", "Skip Task", "skipped", "already done", None)
        msg = r.to_telegram()
        assert "⏭" in msg

    def test_to_telegram_no_path_skips_file_line(self):
        r = ActionResult("id4", "No File", "executed", "detail", None)
        msg = r.to_telegram()
        assert "No File" in msg
        assert "📄" not in msg

    def test_fields_accessible(self):
        r = ActionResult("task_id", "Title", "executed", "detail", "/p.json")
        assert r.task_id == "task_id"
        assert r.title == "Title"
        assert r.status == "executed"
        assert r.detail == "detail"
        assert r.output_path == "/p.json"


# ── Init & State ──────────────────────────────────────────────────────────────

class TestScheduledTaskRunnerInit:
    def test_empty_state_on_fresh_start(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        assert runner._state == {}

    def test_loads_existing_state(self, runner_env):
        state_file = runner_env / ".task_state.json"
        state_file.write_text('{"param_review_20": "2026-04-01"}', encoding="utf-8")
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        assert runner._done("param_review_20")

    def test_not_done_when_key_missing(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        assert not runner._done("nonexistent_key")

    def test_mark_and_done(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        runner._mark("test_key")
        assert runner._done("test_key")

    def test_save_state_persists(self, runner_env):
        runner1 = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        runner1._mark("walk_forward_50")
        runner1._save_state()

        runner2 = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        assert runner2._done("walk_forward_50")

    def test_corrupted_state_file_handled(self, runner_env):
        state_file = runner_env / ".task_state.json"
        state_file.write_text("not json", encoding="utf-8")
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        assert runner._state == {}


# ── run() ─────────────────────────────────────────────────────────────────────

class TestScheduledTaskRunnerRun:
    def test_no_results_with_no_trades(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        results = runner.run(_mr_stats(total=0))
        assert results == []

    def test_no_results_below_thresholds(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        results = runner.run(_mr_stats(total=5, wins=3, pnl=100.0))
        assert results == []

    def test_param_review_triggered_at_20_trades(self, runner_env):
        db = runner_env / "j.db"
        _save_trades(db, count=25)
        runner = ScheduledTaskRunner(journal_path=str(db))
        results = runner.run(_mr_stats(total=25, wins=15, pnl=500.0, sig=15, sl=5, tp=5))
        assert any(r.task_id == "param_review_20" for r in results)
        assert any(r.status == "executed" for r in results)

    def test_param_review_not_repeated(self, runner_env):
        db = runner_env / "j.db"
        _save_trades(db, count=25)
        runner = ScheduledTaskRunner(journal_path=str(db))
        runner._mark("param_review_20")
        results = runner.run(_mr_stats(total=25, wins=15, pnl=500.0))
        assert not any(r.task_id == "param_review_20" for r in results)

    def test_wr_alert_triggered_below_40_pct(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=15, wins=4, pnl=50.0, sig=8, sl=5, tp=2)
        stats["wr_pct"] = 26.7
        results = runner.run(stats)
        assert any(r.task_id == "wr_alert" for r in results)

    def test_wr_alert_not_triggered_below_10_trades(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=8, wins=2, pnl=-200.0)
        stats["wr_pct"] = 25.0
        results = runner.run(stats)
        assert not any(r.task_id == "wr_alert" for r in results)

    def test_pnl_alert_triggered_on_negative_pnl(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=12, wins=5, pnl=-2000.0, sig=6, sl=4, tp=2)
        stats["wr_pct"] = 41.7
        results = runner.run(stats)
        assert any(r.task_id == "pnl_alert" for r in results)

    def test_sl_review_triggered_on_high_sl_ratio(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        # sl_count (7) > sig_count (2) + tp_count (1) = 3
        stats = _mr_stats(total=10, wins=4, pnl=0.0, sl=7, sig=2, tp=1)
        stats["wr_pct"] = 40.0
        results = runner.run(stats)
        assert any("sl_review" in r.task_id for r in results)

    def test_walk_forward_triggered_at_50_trades(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=55, wins=30, pnl=3000.0, sig=25, sl=15, tp=15)
        stats["wr_pct"] = 54.5
        results = runner.run(stats)
        assert any(r.task_id == "walk_forward_50" for r in results)

    def test_walk_forward_creates_trigger_file(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=55, wins=30, pnl=3000.0)
        stats["wr_pct"] = 54.5
        results = runner.run(stats)
        wf_result = next((r for r in results if r.task_id == "walk_forward_50"), None)
        if wf_result and wf_result.output_path:
            assert Path(wf_result.output_path).exists()

    def test_all_results_are_action_result_instances(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=55, wins=30, pnl=3000.0)
        stats["wr_pct"] = 54.5
        results = runner.run(stats)
        for r in results:
            assert isinstance(r, ActionResult)

    def test_state_saved_after_tasks_run(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=55, wins=30, pnl=3000.0)
        stats["wr_pct"] = 54.5
        runner.run(stats)
        state_file = runner_env / ".task_state.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert "walk_forward_50" in state

    def test_wr_alert_saves_json_file(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=15, wins=4, pnl=50.0, sig=8, sl=5, tp=2)
        stats["wr_pct"] = 26.7
        results = runner.run(stats)
        wr_result = next((r for r in results if r.task_id == "wr_alert"), None)
        assert wr_result is not None
        if wr_result.output_path:
            assert Path(wr_result.output_path).exists()

    def test_pnl_alert_saves_json_file(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=12, wins=5, pnl=-2000.0, sig=6, sl=4, tp=2)
        stats["wr_pct"] = 41.7
        results = runner.run(stats)
        pnl_result = next((r for r in results if r.task_id == "pnl_alert"), None)
        assert pnl_result is not None
        if pnl_result.output_path:
            assert Path(pnl_result.output_path).exists()

    def test_sl_review_saves_json_file(self, runner_env):
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=10, wins=4, pnl=0.0, sl=7, sig=2, tp=1)
        stats["wr_pct"] = 40.0
        results = runner.run(stats)
        sl_result = next((r for r in results if "sl_review" in r.task_id), None)
        assert sl_result is not None


# ── Auto-apply (live_params integration) ────────────────────────────────────

class TestAutoApply:
    def test_wr_alert_applies_params_and_requests_restart(self, runner_env):
        import src.config.live_params as lp
        before = lp.load()["strategy_params"]

        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=15, wins=4, pnl=50.0, sig=8, sl=5, tp=2)
        stats["wr_pct"] = 26.7
        runner.run(stats)

        after = lp.load()["strategy_params"]
        assert after["mr_rsi_oversold_fast"] < before["mr_rsi_oversold_fast"]
        assert after["mr_rsi_oversold_slow"] < before["mr_rsi_oversold_slow"]
        assert after["mr_vol_mult"] > before["mr_vol_mult"]
        assert lp.restart_requested()

    def test_wr_alert_step_capped_to_20_percent(self, runner_env):
        import src.config.live_params as lp
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=15, wins=4, pnl=50.0, sig=8, sl=5, tp=2)
        stats["wr_pct"] = 26.7
        runner.run(stats)

        after = lp.load()["strategy_params"]
        # default mr_vol_mult 2.0 * 1.2 = 2.4 (within bound, not clamped)
        assert after["mr_vol_mult"] == pytest.approx(2.4, abs=0.01)

    def test_wr_alert_never_crosses_rsi_bounds(self, runner_env):
        """Repeated firings must plateau at PARAM_BOUNDS, never exceed them."""
        import src.config.live_params as lp
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=15, wins=4, pnl=50.0, sig=8, sl=5, tp=2)
        stats["wr_pct"] = 26.7
        for i in range(30):
            stats_i = dict(stats)
            stats_i["total"] = 15 + i * 5  # advance the 5-trade dedup bucket
            runner.run(stats_i)

        after = lp.load()["strategy_params"]
        fast_lo, fast_hi = lp.PARAM_BOUNDS["mr_rsi_oversold_fast"]
        slow_lo, slow_hi = lp.PARAM_BOUNDS["mr_rsi_oversold_slow"]
        vol_lo, vol_hi = lp.PARAM_BOUNDS["mr_vol_mult"]
        assert fast_lo <= after["mr_rsi_oversold_fast"] <= fast_hi
        assert slow_lo <= after["mr_rsi_oversold_slow"] <= slow_hi
        assert vol_lo <= after["mr_vol_mult"] <= vol_hi
        assert after["mr_rsi_oversold_fast"] < after["mr_rsi_oversold_slow"]

    def test_pnl_alert_applies_tp_rr_multiplier(self, runner_env):
        import src.config.live_params as lp
        before = lp.load()["risk"]["tp_rr_multiplier"]

        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=12, wins=5, pnl=-2000.0, sig=6, sl=4, tp=2)
        stats["wr_pct"] = 41.7
        runner.run(stats)

        after = lp.load()["risk"]["tp_rr_multiplier"]
        assert after > before
        assert lp.restart_requested()

    def test_sl_review_applies_sl_floor_pct(self, runner_env):
        import src.config.live_params as lp
        before = lp.load()["risk"]["sl_floor_pct"]

        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=10, wins=4, pnl=0.0, sl=7, sig=2, tp=1)
        stats["wr_pct"] = 40.0
        runner.run(stats)

        after = lp.load()["risk"]["sl_floor_pct"]
        assert after > before
        assert lp.restart_requested()

    def test_audit_log_records_applied_changes(self, runner_env):
        import src.config.live_params as lp
        runner = ScheduledTaskRunner(journal_path=str(runner_env / "j.db"))
        stats = _mr_stats(total=10, wins=4, pnl=0.0, sl=7, sig=2, tp=1)
        stats["wr_pct"] = 40.0
        runner.run(stats)

        assert lp.AUDIT_LOG.exists()
        lines = lp.AUDIT_LOG.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 1
        entry = json.loads(lines[0])
        assert {"date", "trigger", "param", "old", "new", "reason"} <= entry.keys()
