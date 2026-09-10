"""
.strategy_params.json → CLI 인자 문자열 렌더러.

start_agent_watchdog.ps1 / start_agent.bat 가 매 (재)시작마다 이 스크립트를
호출해 --strategy-params 및 SL/TP 리스크 인자를 동적으로 구성한다.
ScheduledTaskRunner(src/report/task_runner.py)가 .strategy_params.json 을
갱신하면, 다음 재시작부터 이 스크립트가 새 값을 그대로 반영한다.

src 패키지에 의존하지 않는 독립 스크립트로 유지한다 (배치/파워셸에서
작업 디렉터리와 무관하게 호출 가능해야 하므로).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARAMS_FILE = ROOT / ".strategy_params.json"

DEFAULT_STRATEGY_PARAMS = {
    "adx_trend_threshold": 25.0,
    "mr_vol_mult": 2.0,
    "mr_rsi_oversold_fast": 20.0,
    "mr_rsi_oversold_slow": 30.0,
    "mr_rsi_exit": 55.0,
    "mr_no_entry_hours_utc": [17, 18, 19, 20],
    "mr_sma_period": 0,      # 0 = long-term trend filter disabled (backward compatible)
    "mr_sma_floor": 0.85,
    "sm_vol_mult": 1.5,
    "sm_adx_threshold": 28.0,
}
DEFAULT_RISK = {
    "sl_floor_pct": 3.0,
    "sl_ceiling_pct": 1.5,
    "atr_multiplier": 2.0,
    "tp_rr_multiplier": 1.5,
    "time_stop_minutes": 60.0,
    "time_stop_loss_pct": 0.5,
    "consecutive_loss_limit": 3,             # 0 = circuit breaker disabled
    "consecutive_loss_cooldown_minutes": 720,  # 12h
}


def main() -> None:
    if PARAMS_FILE.exists():
        data = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    else:
        data = {}
    strategy_params = {**DEFAULT_STRATEGY_PARAMS, **data.get("strategy_params", {})}
    risk = {**DEFAULT_RISK, **data.get("risk", {})}

    sp_json = json.dumps(strategy_params, ensure_ascii=False).replace('"', '\\"')
    parts = [
        f'--strategy-params "{sp_json}"',
        f"--sl-floor-pct {risk['sl_floor_pct']}",
        f"--sl-ceiling-pct {risk['sl_ceiling_pct']}",
        f"--atr-multiplier {risk['atr_multiplier']}",
        f"--tp-rr-multiplier {risk['tp_rr_multiplier']}",
        f"--time-stop-minutes {risk['time_stop_minutes']}",
        f"--time-stop-loss-pct {risk['time_stop_loss_pct']}",
        f"--consecutive-loss-limit {risk['consecutive_loss_limit']}",
        f"--consecutive-loss-cooldown-min {risk['consecutive_loss_cooldown_minutes']}",
    ]
    print(" ".join(parts))


if __name__ == "__main__":
    main()
