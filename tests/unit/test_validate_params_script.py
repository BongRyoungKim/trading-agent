"""Regression guard for scripts/validate_params.py's strategy-key whitelist.

_STRATEGY_KEYS silently drops any strategy_params key it doesn't list when
building the baseline for a validation run (see _strategy_kwargs()). This bit
us for real: mr_rsi_exit_fast was added as a tunable in
src.config.live_params.PARAM_BOUNDS but forgotten here, so a validation run
launched via this script would have silently ignored it in the baseline.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

import src.config.live_params as lp
from src.config.live_params import DEFAULTS, PARAM_BOUNDS, RISK_KEYS

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
_SCRIPT_PATH = _SCRIPTS_DIR / "validate_params.py"


def _load_validate_params_module():
    # validate_params.py does `from render_launch_args import ...` assuming
    # it's run as `python scripts/validate_params.py` (which puts scripts/ on
    # sys.path automatically) — replicate that when loading it by file path.
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("validate_params_script", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_strategy_keys_covers_every_tunable_strategy_param():
    module = _load_validate_params_module()
    tunable_strategy_params = set(PARAM_BOUNDS) - set(RISK_KEYS)
    missing = tunable_strategy_params - set(module._STRATEGY_KEYS)
    assert not missing, (
        f"scripts/validate_params.py's _STRATEGY_KEYS is missing tunable "
        f"strategy params from PARAM_BOUNDS: {missing}"
    )


def _regime_adaptive_sub_strategy_params() -> set[str]:
    """Every mr_*/sm_* kwarg RegimeAdaptiveStrategy forwards to a sub-strategy."""
    from src.strategy.regime_adaptive import RegimeAdaptiveStrategy

    return {
        name
        for name in inspect.signature(RegimeAdaptiveStrategy.__init__).parameters
        if name.startswith(("mr_", "sm_"))
    }


def test_every_sub_strategy_param_is_wired_end_to_end():
    """Generic wiring guard for the whole live parameter pipeline.

    A sub-strategy parameter only reaches live trading if it is declared in
    all three places below; miss one and the failure is *silent* (the value
    is quietly dropped and the sub-strategy runs on its own default). This
    already happened once with mr_rsi_exit_fast, and the same class of bug
    was re-checked for sm_vol_mult/sm_adx_threshold during the
    SwingMomentum diagnosis. Enumerating the constructor signature means new
    parameters are covered automatically instead of by remembering to edit
    a list.

      render_launch_args.DEFAULT_STRATEGY_PARAMS
          -> what entrypoint.sh actually passes to `python -m src.main`
      live_params.DEFAULTS["strategy_params"]
          -> what the dashboard / task runner reads back as current state
      validate_params._STRATEGY_KEYS
          -> what the backtest gate compares baseline against candidate on
    """
    module = _load_validate_params_module()
    import render_launch_args

    expected = _regime_adaptive_sub_strategy_params()
    locations = {
        "render_launch_args.DEFAULT_STRATEGY_PARAMS":
            set(render_launch_args.DEFAULT_STRATEGY_PARAMS),
        "live_params.DEFAULTS['strategy_params']":
            set(DEFAULTS["strategy_params"]),
        "validate_params._STRATEGY_KEYS":
            set(module._STRATEGY_KEYS),
    }
    missing = {
        where: sorted(expected - declared)
        for where, declared in locations.items()
        if expected - declared
    }
    assert not missing, (
        "RegimeAdaptiveStrategy sub-strategy parameters are not wired "
        f"end-to-end: {missing}"
    )


class _FakeCheck:
    def __init__(self, name: str, passed: bool, detail: str) -> None:
        self.name, self.passed, self.detail = name, passed, detail


class _FakeGate:
    def __init__(self, passed: bool) -> None:
        self.passed = passed
        self.checks = [_FakeCheck("profit_factor", passed, "ok")]

    def summary(self) -> str:
        return "gate summary"


class _FakeResult:
    _METRICS = {
        "profit_factor": 1.0, "sharpe_ratio": 1.0, "max_drawdown_pct": 1.0,
        "win_rate_pct": 50.0, "total_trades": 10,
    }

    def __init__(self, passed: bool) -> None:
        self.gate = _FakeGate(passed)
        self.baseline_metrics = dict(self._METRICS)
        self.candidate_metrics = dict(self._METRICS)

    def report(self) -> str:
        return "report"


@pytest.fixture
def isolated_lp(tmp_path, monkeypatch):
    """Same isolation pattern as tests/unit/test_live_params.py — redirect
    every module-level file path validate_params.py reads/writes through
    src.config.live_params into tmp_path."""
    monkeypatch.setattr(lp, "PARAMS_FILE", tmp_path / ".strategy_params.json")
    monkeypatch.setattr(lp, "AUDIT_LOG", tmp_path / "reports" / "param_change_log.jsonl")
    monkeypatch.setattr(lp, "RESTART_FLAG", tmp_path / ".restart_requested")
    monkeypatch.setattr(lp, "PENDING_FILE", tmp_path / "reports" / "pending_param_change.json")
    monkeypatch.setattr(lp, "VALIDATION_FILE", tmp_path / "reports" / "pending_param_validation.json")
    return tmp_path


class TestAutoValidationCacheSkip:
    """validate_params.py is meant to be re-run periodically by a host cron
    (against the one-shot `backtest` compose service) so proposals get
    validated without anyone SSHing in. Re-running the heavy backtest on
    every tick while nobody has acted on the proposal yet would be wasteful
    on the 1GB-RAM production VM, so a cached result for the still-current
    proposal must be reused instead of triggering a new backtest."""

    def _run_main(self, module, monkeypatch, tmp_path, calls) -> int:
        monkeypatch.setattr(module, "PARAMS_FILE", tmp_path / ".strategy_params.json")
        monkeypatch.setattr(
            module, "validate_param_change",
            lambda **kw: calls.append(kw) or _FakeResult(True),
        )
        monkeypatch.setattr(sys, "argv", ["validate_params.py", "--pending", str(lp.PENDING_FILE)])
        return module.main()

    def test_skips_backtest_when_cached_result_matches_pending(self, isolated_lp, monkeypatch, capsys):
        module = _load_validate_params_module()
        lp.propose_pending("mr_vol_mult", 2.5, trigger="t", reason="r")
        lp.save_validation_result(
            passed=True, checks=[], baseline_metrics={}, candidate_metrics={},
            fingerprint=lp.pending_fingerprint(),
        )

        calls: list[dict] = []
        rc = self._run_main(module, monkeypatch, isolated_lp, calls)

        assert rc == 0
        assert calls == []
        assert "재실행 생략" in capsys.readouterr().out

    def test_runs_and_caches_when_pending_changed_since_last_validation(self, isolated_lp, monkeypatch):
        module = _load_validate_params_module()
        lp.propose_pending("mr_vol_mult", 2.5, trigger="t", reason="r")
        old_fingerprint = lp.pending_fingerprint()
        lp.save_validation_result(
            passed=True, checks=[], baseline_metrics={}, candidate_metrics={}, fingerprint=old_fingerprint,
        )
        lp.propose_pending("sl_floor_pct", 3.5, trigger="t", reason="r")
        assert lp.pending_fingerprint() != old_fingerprint

        calls: list[dict] = []
        rc = self._run_main(module, monkeypatch, isolated_lp, calls)

        assert rc == 0
        assert len(calls) == 1
        saved = lp.load_validation_result()
        assert saved["pending_fingerprint"] == lp.pending_fingerprint()

    def test_force_reruns_even_when_cache_matches(self, isolated_lp, monkeypatch):
        module = _load_validate_params_module()
        lp.propose_pending("mr_vol_mult", 2.5, trigger="t", reason="r")
        lp.save_validation_result(
            passed=True, checks=[], baseline_metrics={}, candidate_metrics={},
            fingerprint=lp.pending_fingerprint(),
        )

        calls: list[dict] = []
        monkeypatch.setattr(module, "PARAMS_FILE", isolated_lp / ".strategy_params.json")
        monkeypatch.setattr(
            module, "validate_param_change",
            lambda **kw: calls.append(kw) or _FakeResult(True),
        )
        monkeypatch.setattr(
            sys, "argv", ["validate_params.py", "--pending", str(lp.PENDING_FILE), "--force"],
        )

        rc = module.main()

        assert rc == 0
        assert len(calls) == 1

    def test_cached_failed_result_is_not_applied(self, isolated_lp, monkeypatch):
        module = _load_validate_params_module()
        lp.propose_pending("mr_vol_mult", 2.5, trigger="t", reason="r")
        lp.save_validation_result(
            passed=False, checks=[], baseline_metrics={}, candidate_metrics={},
            fingerprint=lp.pending_fingerprint(),
        )

        calls: list[dict] = []
        monkeypatch.setattr(module, "PARAMS_FILE", isolated_lp / ".strategy_params.json")
        monkeypatch.setattr(
            module, "validate_param_change",
            lambda **kw: calls.append(kw) or _FakeResult(True),
        )
        monkeypatch.setattr(
            sys, "argv", ["validate_params.py", "--pending", str(lp.PENDING_FILE), "--apply"],
        )

        rc = module.main()

        assert rc == 1
        assert calls == []
        assert lp.has_pending_change() is True  # never applied
