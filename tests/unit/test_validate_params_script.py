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
