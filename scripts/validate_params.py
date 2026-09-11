"""
CLI: validate a proposed strategy/risk parameter change against what is
currently live, before applying it.

    python scripts/validate_params.py --pending reports/pending_param_change.json
    python scripts/validate_params.py --pending reports/pending_param_change.json --apply

Reads the current live baseline from .strategy_params.json (falling back to
this same directory's render_launch_args.py defaults for missing keys,
matching how the live entrypoint resolves parameters), and the candidate
from the given pending-change file (same {"strategy_params": {...},
"risk": {...}} shape src.config.live_params writes).

Prints a pass/fail report and exits 0 (pass) or 1 (fail). With --apply, a
passing candidate is committed live via src.config.live_params.propose_and_apply
(one call per changed param, preserving its step-cap/clamp/audit-log
behaviour) and the pending file is cleared; a failing candidate is never
applied even if --apply is passed.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Windows consoles often default stdout to a legacy codepage (e.g. cp949)
# that cannot encode "-" or other non-ASCII punctuation used below; this
# crashed the exact same way in an earlier script this session
# (ampere_retry.py) before this fix was applied there too.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from render_launch_args import DEFAULT_RISK, DEFAULT_STRATEGY_PARAMS, PARAMS_FILE  # noqa: E402

from src.backtest.exit_backtest_engine import ExitBacktestConfig  # noqa: E402
from src.backtest.param_validator import validate_param_change  # noqa: E402
from src.config import live_params as lp  # noqa: E402

DEFAULT_SYMBOLS = ["BTC/KRW", "ETH/KRW", "XRP/KRW", "SUI/KRW"]
_STRATEGY_KEYS = (
    "adx_trend_threshold", "mr_vol_mult", "mr_rsi_oversold_fast",
    "mr_rsi_oversold_slow", "mr_rsi_exit", "mr_no_entry_hours_utc",
    "mr_sma_period", "mr_sma_floor", "sm_vol_mult", "sm_adx_threshold",
)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _strategy_kwargs(strategy_params: dict) -> dict:
    return {k: strategy_params[k] for k in _STRATEGY_KEYS if k in strategy_params}


def _risk_config(risk: dict) -> ExitBacktestConfig:
    return ExitBacktestConfig(
        sl_ceiling_pct=Decimal(str(risk["sl_ceiling_pct"])),
        sl_floor_pct=Decimal(str(risk["sl_floor_pct"])),
        atr_multiplier=float(risk["atr_multiplier"]),
        tp_rr_multiplier=Decimal(str(risk["tp_rr_multiplier"])),
        time_stop_minutes=float(risk.get("time_stop_minutes", 60.0)),
        time_stop_loss_pct=Decimal(str(risk.get("time_stop_loss_pct", 0.5))),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pending", required=True, help="Path to the proposed change JSON")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument(
        "--data-split", default="validation", choices=["train", "validation", "holdout"],
        help="holdout is a final, one-time check per docs/research-protocol.md — "
             "do not use it repeatedly while iterating on a candidate.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="If the gate passes, commit the candidate live and clear the pending file.",
    )
    args = parser.parse_args()

    live = _load_json(PARAMS_FILE)
    baseline_sp = {**DEFAULT_STRATEGY_PARAMS, **live.get("strategy_params", {})}
    baseline_risk = {**DEFAULT_RISK, **live.get("risk", {})}

    pending = _load_json(Path(args.pending))
    if not pending:
        print(f"No pending change found at {args.pending}")
        return 1
    candidate_sp = {**baseline_sp, **pending.get("strategy_params", {})}
    candidate_risk = {**baseline_risk, **pending.get("risk", {})}

    result = validate_param_change(
        symbols=args.symbols,
        baseline_strategy_kwargs=_strategy_kwargs(baseline_sp),
        baseline_risk_config=_risk_config(baseline_risk),
        candidate_strategy_kwargs=_strategy_kwargs(candidate_sp),
        candidate_risk_config=_risk_config(candidate_risk),
        data_split_part=args.data_split,
    )

    print(result.report())

    # Cache the result regardless of pass/fail so the dashboard's
    # /api/params/pending can show it without re-running this (heavy)
    # backtest itself.
    lp.save_validation_result(
        passed=result.gate.passed,
        checks=[{"name": c.name, "passed": c.passed, "detail": c.detail} for c in result.gate.checks],
        baseline_metrics=result.baseline_metrics,
        candidate_metrics=result.candidate_metrics,
    )

    if not result.gate.passed:
        print("\nGate FAILED — not applying.")
        return 1

    if args.apply:
        lp.apply_pending("validate_params.py --apply")
        print("\nGate PASSED — applied live, restart requested.")
    else:
        print("\nGate PASSED — rerun with --apply to commit live (or approve from the dashboard).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
