"""Unit tests for src.config.live_params — clamp, step-cap, audit trail, restart flag."""
from __future__ import annotations

import json

import pytest

import src.config.live_params as lp


@pytest.fixture(autouse=True)
def isolate_files(tmp_path, monkeypatch):
    """Redirect all module-level file paths into tmp_path for every test."""
    monkeypatch.setattr(lp, "PARAMS_FILE", tmp_path / ".strategy_params.json")
    monkeypatch.setattr(lp, "AUDIT_LOG", tmp_path / "reports" / "param_change_log.jsonl")
    monkeypatch.setattr(lp, "RESTART_FLAG", tmp_path / ".restart_requested")
    monkeypatch.setattr(lp, "PENDING_FILE", tmp_path / "reports" / "pending_param_change.json")
    monkeypatch.setattr(lp, "VALIDATION_FILE", tmp_path / "reports" / "pending_param_validation.json")
    return tmp_path


class TestLoadSave:
    def test_load_returns_defaults_when_no_file(self):
        data = lp.load()
        assert data["strategy_params"] == lp.DEFAULTS["strategy_params"]
        assert data["risk"] == lp.DEFAULTS["risk"]

    def test_save_then_load_roundtrips(self):
        data = lp.load()
        data["risk"]["sl_floor_pct"] = 3.5
        lp.save(data)
        assert lp.load()["risk"]["sl_floor_pct"] == 3.5

    def test_load_fills_missing_keys_with_defaults(self):
        lp.PARAMS_FILE.write_text(json.dumps({"strategy_params": {"mr_vol_mult": 2.5}}), encoding="utf-8")
        data = lp.load()
        assert data["strategy_params"]["mr_vol_mult"] == 2.5
        assert data["strategy_params"]["mr_rsi_exit"] == lp.DEFAULTS["strategy_params"]["mr_rsi_exit"]

    def test_load_recovers_from_corrupted_file(self):
        lp.PARAMS_FILE.write_text("not json", encoding="utf-8")
        data = lp.load()
        assert data["strategy_params"] == lp.DEFAULTS["strategy_params"]

    def test_get_current_reads_correct_section(self):
        assert lp.get_current("mr_vol_mult") == lp.DEFAULTS["strategy_params"]["mr_vol_mult"]
        assert lp.get_current("sl_floor_pct") == lp.DEFAULTS["risk"]["sl_floor_pct"]


class TestClampAndStepCap:
    def test_unknown_param_raises(self):
        with pytest.raises(ValueError):
            lp.propose_and_apply("not_a_real_param", 1.0, trigger="t", reason="r")

    def test_change_within_step_and_bounds_applies_exactly(self):
        result = lp.propose_and_apply("mr_vol_mult", 2.2, trigger="t", reason="r")
        assert result["changed"] is True
        assert result["old"] == 2.0
        assert result["new"] == pytest.approx(2.2)

    def test_step_capped_to_20_percent_of_old_value(self):
        # target requests +100% (2.0 -> 4.0) but step cap limits to +20% (2.0 -> 2.4)
        result = lp.propose_and_apply("mr_vol_mult", 4.0, trigger="t", reason="r")
        assert result["new"] == pytest.approx(2.4)

    def test_clamped_to_upper_bound(self):
        # Repeated large jumps should never exceed PARAM_BOUNDS upper bound.
        for _ in range(20):
            current = lp.get_current("mr_vol_mult")
            lp.propose_and_apply("mr_vol_mult", current * 2, trigger="t", reason="r")
        _, hi = lp.PARAM_BOUNDS["mr_vol_mult"]
        assert lp.get_current("mr_vol_mult") <= hi

    def test_clamped_to_lower_bound(self):
        for _ in range(20):
            current = lp.get_current("mr_rsi_oversold_fast")
            lp.propose_and_apply("mr_rsi_oversold_fast", current * 0.1, trigger="t", reason="r")
        lo, _ = lp.PARAM_BOUNDS["mr_rsi_oversold_fast"]
        assert lp.get_current("mr_rsi_oversold_fast") >= lo

    def test_no_change_when_already_at_bound(self):
        lo, _ = lp.PARAM_BOUNDS["mr_rsi_oversold_fast"]
        data = lp.load()
        data["strategy_params"]["mr_rsi_oversold_fast"] = lo
        lp.save(data)
        result = lp.propose_and_apply("mr_rsi_oversold_fast", lo * 0.1, trigger="t", reason="r")
        assert result["changed"] is False
        assert result["new"] == lo

    def test_applies_to_risk_section(self):
        result = lp.propose_and_apply("sl_floor_pct", 3.5, trigger="t", reason="r")
        assert result["changed"] is True
        assert lp.load()["risk"]["sl_floor_pct"] == pytest.approx(3.5)


class TestRsiGapSafety:
    def test_enforce_rsi_gap_noop_when_gap_sufficient(self):
        assert lp.enforce_rsi_gap() is None

    def test_enforce_rsi_gap_fixes_crossed_values(self):
        data = lp.load()
        data["strategy_params"]["mr_rsi_oversold_fast"] = 29.0
        data["strategy_params"]["mr_rsi_oversold_slow"] = 30.0
        lp.save(data)
        result = lp.enforce_rsi_gap(min_gap=2.0)
        assert result is not None
        after = lp.load()["strategy_params"]
        assert after["mr_rsi_oversold_slow"] - after["mr_rsi_oversold_fast"] >= 2.0

    def test_enforce_rsi_gap_respects_lower_bound(self):
        lo, _ = lp.PARAM_BOUNDS["mr_rsi_oversold_fast"]
        data = lp.load()
        data["strategy_params"]["mr_rsi_oversold_fast"] = lo
        data["strategy_params"]["mr_rsi_oversold_slow"] = lo + 0.5
        lp.save(data)
        lp.enforce_rsi_gap(min_gap=2.0)
        assert lp.get_current("mr_rsi_oversold_fast") >= lo


class TestAuditLog:
    def test_applied_change_appends_audit_entry(self):
        lp.propose_and_apply("mr_vol_mult", 2.2, trigger="wr_alert", reason="WR low")
        lines = lp.AUDIT_LOG.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["param"] == "mr_vol_mult"
        assert entry["trigger"] == "wr_alert"
        assert entry["old"] == 2.0

    def test_no_op_change_does_not_append_audit(self):
        lo, _ = lp.PARAM_BOUNDS["mr_rsi_oversold_fast"]
        data = lp.load()
        data["strategy_params"]["mr_rsi_oversold_fast"] = lo
        lp.save(data)
        lp.propose_and_apply("mr_rsi_oversold_fast", lo * 0.1, trigger="t", reason="r")
        assert not lp.AUDIT_LOG.exists()


class TestProposePending:
    def test_pending_does_not_touch_live_params(self):
        lp.propose_pending("mr_vol_mult", 2.2, trigger="t", reason="r")
        assert lp.get_current("mr_vol_mult") == lp.DEFAULTS["strategy_params"]["mr_vol_mult"]

    def test_no_pending_file_initially(self):
        assert lp.has_pending_change() is False

    def test_changed_proposal_creates_pending_file(self):
        result = lp.propose_pending("mr_vol_mult", 2.2, trigger="t", reason="r")
        assert result["changed"] is True
        assert lp.has_pending_change() is True

    def test_pending_uses_same_step_cap_and_clamp_as_apply(self):
        # +100% request -> step-capped to +20%, same math as propose_and_apply.
        result = lp.propose_pending("mr_vol_mult", 4.0, trigger="t", reason="r")
        assert result["new"] == pytest.approx(2.4)

    def test_unknown_param_raises(self):
        with pytest.raises(ValueError):
            lp.propose_pending("not_a_real_param", 1.0, trigger="t", reason="r")

    def test_no_op_when_already_at_bound_does_not_create_pending_file(self):
        lo, _ = lp.PARAM_BOUNDS["mr_rsi_oversold_fast"]
        data = lp.load()
        data["strategy_params"]["mr_rsi_oversold_fast"] = lo
        lp.save(data)
        result = lp.propose_pending("mr_rsi_oversold_fast", lo * 0.1, trigger="t", reason="r")
        assert result["changed"] is False
        assert lp.has_pending_change() is False

    def test_pending_file_records_trigger_and_reason(self):
        lp.propose_pending("mr_vol_mult", 2.2, trigger="wr_alert", reason="WR low")
        pending = json.loads(lp.PENDING_FILE.read_text(encoding="utf-8"))
        assert pending["strategy_params"]["mr_vol_mult"] == pytest.approx(2.2)
        assert pending["_meta"]["mr_vol_mult"]["trigger"] == "wr_alert"

    def test_multiple_proposals_accumulate_in_one_pending_file(self):
        lp.propose_pending("mr_vol_mult", 2.2, trigger="t1", reason="r1")
        lp.propose_pending("sl_floor_pct", 3.5, trigger="t2", reason="r2")
        pending = json.loads(lp.PENDING_FILE.read_text(encoding="utf-8"))
        assert pending["strategy_params"]["mr_vol_mult"] == pytest.approx(2.2)
        assert pending["risk"]["sl_floor_pct"] == pytest.approx(3.5)

    def test_clear_pending_removes_file(self):
        lp.propose_pending("mr_vol_mult", 2.2, trigger="t", reason="r")
        lp.clear_pending()
        assert lp.has_pending_change() is False

    def test_clear_pending_when_absent_is_safe(self):
        lp.clear_pending()  # should not raise
        assert lp.has_pending_change() is False


class TestValidationResult:
    def test_no_result_initially(self):
        assert lp.load_validation_result() is None

    def test_save_then_load_roundtrips(self):
        lp.save_validation_result(
            passed=True, checks=[{"name": "profit_factor", "passed": True, "detail": "ok"}],
            baseline_metrics={"profit_factor": 1.0}, candidate_metrics={"profit_factor": 1.2},
        )
        result = lp.load_validation_result()
        assert result["passed"] is True
        assert result["baseline_metrics"]["profit_factor"] == 1.0
        assert result["candidate_metrics"]["profit_factor"] == 1.2
        assert "validated_at" in result

    def test_clear_removes_file(self):
        lp.save_validation_result(passed=False, checks=[], baseline_metrics={}, candidate_metrics={})
        lp.clear_validation_result()
        assert lp.load_validation_result() is None

    def test_clear_when_absent_is_safe(self):
        lp.clear_validation_result()  # should not raise
        assert lp.load_validation_result() is None


class TestApplyPending:
    def test_no_pending_change_is_a_safe_noop(self):
        assert lp.apply_pending() == []
        assert not lp.restart_requested()

    def test_applies_all_pending_strategy_and_risk_params(self):
        lp.propose_pending("mr_vol_mult", 2.2, trigger="t", reason="r")
        lp.propose_pending("sl_floor_pct", 3.5, trigger="t", reason="r")
        summaries = lp.apply_pending()
        assert len(summaries) == 2
        after = lp.load()
        assert after["strategy_params"]["mr_vol_mult"] == pytest.approx(2.2)
        assert after["risk"]["sl_floor_pct"] == pytest.approx(3.5)

    def test_requests_restart_when_something_changed(self):
        lp.propose_pending("mr_vol_mult", 2.2, trigger="t", reason="r")
        lp.apply_pending()
        assert lp.restart_requested()

    def test_no_restart_requested_when_nothing_to_apply(self):
        lp.apply_pending()
        assert not lp.restart_requested()

    def test_clears_pending_and_validation_after_applying(self):
        lp.propose_pending("mr_vol_mult", 2.2, trigger="t", reason="r")
        lp.save_validation_result(passed=True, checks=[], baseline_metrics={}, candidate_metrics={})
        lp.apply_pending()
        assert not lp.has_pending_change()
        assert lp.load_validation_result() is None

    def test_writes_audit_log_entry_for_each_applied_param(self):
        lp.propose_pending("mr_vol_mult", 2.2, trigger="t", reason="r")
        lp.apply_pending()
        lines = lp.AUDIT_LOG.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["trigger"] == "performance_gate_approved"


class TestRestartFlag:
    def test_not_requested_initially(self):
        assert lp.restart_requested() is False

    def test_request_sets_flag_with_reason(self):
        lp.request_restart("param changed")
        assert lp.restart_requested() is True
        assert lp.read_restart_reason() == "param changed"

    def test_clear_removes_flag(self):
        lp.request_restart("param changed")
        lp.clear_restart_request()
        assert lp.restart_requested() is False

    def test_clear_when_not_set_is_safe(self):
        lp.clear_restart_request()  # should not raise
        assert lp.restart_requested() is False
