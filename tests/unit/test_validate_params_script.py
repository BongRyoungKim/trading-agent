"""Regression guard for scripts/validate_params.py's strategy-key whitelist.

_STRATEGY_KEYS silently drops any strategy_params key it doesn't list when
building the baseline for a validation run (see _strategy_kwargs()). This bit
us for real: mr_rsi_exit_fast was added as a tunable in
src.config.live_params.PARAM_BOUNDS but forgotten here, so a validation run
launched via this script would have silently ignored it in the baseline.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from src.config.live_params import PARAM_BOUNDS, RISK_KEYS

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
