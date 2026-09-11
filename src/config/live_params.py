"""
실거래 전략/리스크 파라미터의 단일 소스(SoT).

ScheduledTaskRunner가 승률/PnL/SL비율 경보를 감지하면 이 모듈을 통해
.strategy_params.json 을 직접 갱신한다. 모든 변경은:
  1. PARAM_BOUNDS 범위로 클램프 (하드 세이프가드)
  2. 1회 변동폭을 이전 값의 ±MAX_STEP_FRACTION 으로 제한 (점진적 조정)
  3. reports/param_change_log.jsonl 에 감사 기록
을 거친다. 실제 반영은 워치독이 프로세스를 재기동할 때 발생한다
(.restart_requested 플래그로 신호).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))

_ROOT = Path(__file__).parent.parent.parent
PARAMS_FILE = _ROOT / ".strategy_params.json"
AUDIT_LOG = _ROOT / "reports" / "param_change_log.jsonl"
RESTART_FLAG = _ROOT / ".restart_requested"
# 자동튜너가 제안한 변경 후보 — src/backtest/performance_gate.py 검증을 통과
# (scripts/validate_params.py --apply)하기 전까지는 여기 머무를 뿐 .strategy_
# params.json 에는 절대 반영되지 않는다. propose_and_apply()와 달리 이쪽은
# 라이브에 아무 영향을 주지 않는 순수 제안 단계.
PENDING_FILE = _ROOT / "reports" / "pending_param_change.json"

# 파라미터 1회 변동폭 상한: 이전 값의 ±20%
MAX_STEP_FRACTION = 0.20

DEFAULTS: dict[str, dict[str, Any]] = {
    "strategy_params": {
        "adx_trend_threshold": 25.0,
        "mr_vol_mult": 2.0,
        "mr_rsi_oversold_fast": 20.0,
        "mr_rsi_oversold_slow": 30.0,
        "mr_rsi_exit": 55.0,
        "mr_no_entry_hours_utc": [17, 18, 19, 20],
        "sm_vol_mult": 1.5,
        "sm_adx_threshold": 28.0,
    },
    "risk": {
        "sl_floor_pct": 3.0,
        "sl_ceiling_pct": 1.5,
        "atr_multiplier": 2.0,
        "tp_rr_multiplier": 1.5,
    },
}

# 자동 재조정이 절대 벗어날 수 없는 안전 범위.
PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "adx_trend_threshold":  (15.0, 35.0),
    # mr_vol_mult/sm_vol_mult 하한을 0.3까지 낮춤: 거래량 부족으로 BUY가 전혀
    # 안 나와 운영값을 0.5/0.4까지 수동으로 내려둔 상태였는데, 기존 하한(1.0)이
    # 그보다 높아서 propose_and_apply()가 처음 호출되는 순간 클램프에 의해
    # 0.5/0.4가 1.0으로 강제로 튀어 오르는 문제가 있었다(±20% 스텝캡을 클램프가
    # 덮어씀). 지금 운영 중인 값을 안전 범위 안에 포함시키기 위한 수정.
    "mr_vol_mult":          (0.3, 3.0),
    "mr_rsi_oversold_fast": (10.0, 30.0),
    "mr_rsi_oversold_slow": (20.0, 40.0),
    "mr_rsi_exit":          (45.0, 65.0),
    "sm_vol_mult":          (0.3, 3.0),
    "sm_adx_threshold":     (20.0, 35.0),
    "sl_floor_pct":         (2.0, 4.0),
    "sl_ceiling_pct":       (1.0, 2.0),
    "atr_multiplier":       (1.5, 3.0),
    "tp_rr_multiplier":     (1.2, 3.0),
}

RISK_KEYS = frozenset(DEFAULTS["risk"].keys())

# RSI 과매도 fast/slow 사이 최소 간격 (전략 생성자 검증: fast < slow < exit)
_RSI_MIN_GAP = 2.0


def _section_for(param: str) -> str:
    return "risk" if param in RISK_KEYS else "strategy_params"


def _clamp(param: str, value: float) -> float:
    lo, hi = PARAM_BOUNDS[param]
    return round(min(max(value, lo), hi), 4)


def _cap_step(old: float, target: float) -> float:
    """단일 조정폭을 이전 값의 ±MAX_STEP_FRACTION 으로 제한."""
    if old == 0:
        return target
    max_delta = abs(old) * MAX_STEP_FRACTION
    delta = target - old
    if abs(delta) > max_delta:
        delta = math.copysign(max_delta, delta)
    return round(old + delta, 4)


def load() -> dict[str, dict[str, Any]]:
    """현재 저장된 파라미터를 DEFAULTS와 병합해 반환 (누락 키 보정)."""
    data: dict[str, Any] = {}
    if PARAMS_FILE.exists():
        try:
            data = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    return {
        "strategy_params": {**DEFAULTS["strategy_params"], **data.get("strategy_params", {})},
        "risk": {**DEFAULTS["risk"], **data.get("risk", {})},
    }


def save(data: dict[str, dict[str, Any]]) -> None:
    PARAMS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_current(param: str) -> float:
    data = load()
    section = _section_for(param)
    return data[section].get(param, DEFAULTS[section][param])


def _append_audit(trigger: str, param: str, old: float, new: float, reason: str) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "date":    datetime.now(KST).isoformat(timespec="seconds"),
        "trigger": trigger,
        "param":   param,
        "old":     old,
        "new":     new,
        "reason":  reason,
    }
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def propose_and_apply(
    param: str, target_value: float, trigger: str, reason: str
) -> dict[str, Any]:
    """
    권고값을 스텝캡 + 클램프를 거쳐 실제 반영.

    Returns:
        {"param", "old", "new", "changed"} — changed=False면 이미 안전범위
        경계에 있어 변경이 없었음을 의미 (정상 동작, 오류 아님).
    """
    if param not in PARAM_BOUNDS:
        raise ValueError(f"Unknown tunable parameter: {param}")

    data = load()
    section = _section_for(param)
    old = float(data[section].get(param, DEFAULTS[section][param]))

    stepped = _cap_step(old, target_value)
    clamped = _clamp(param, stepped)
    changed = not math.isclose(clamped, old, rel_tol=1e-6, abs_tol=1e-6)

    if changed:
        data[section][param] = clamped
        save(data)
        _append_audit(trigger=trigger, param=param, old=old, new=clamped, reason=reason)

    return {"param": param, "old": old, "new": clamped, "changed": changed}


def _load_pending() -> dict[str, Any]:
    if PENDING_FILE.exists():
        try:
            return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_pending(data: dict[str, Any]) -> None:
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def propose_pending(
    param: str, target_value: float, trigger: str, reason: str
) -> dict[str, Any]:
    """
    Same step-cap + clamp math as propose_and_apply(), but stages the
    result into PENDING_FILE instead of writing it to PARAMS_FILE.

    Nothing here touches live trading behaviour — scripts/validate_params.py
    must confirm the pending change clears the performance gate
    (src/backtest/performance_gate.py) before anyone calls
    propose_and_apply() to actually commit it.

    Returns the same shape as propose_and_apply(): {"param", "old", "new",
    "changed"} — changed=False means the target was already at/inside the
    safety bounds relative to the current live value, so nothing was staged.
    """
    if param not in PARAM_BOUNDS:
        raise ValueError(f"Unknown tunable parameter: {param}")

    data = load()
    section = _section_for(param)
    old = float(data[section].get(param, DEFAULTS[section][param]))

    stepped = _cap_step(old, target_value)
    clamped = _clamp(param, stepped)
    changed = not math.isclose(clamped, old, rel_tol=1e-6, abs_tol=1e-6)

    if changed:
        pending = _load_pending()
        pending.setdefault(section, {})[param] = clamped
        meta = pending.setdefault("_meta", {})
        meta[param] = {
            "trigger": trigger,
            "reason": reason,
            "proposed_at": datetime.now(KST).isoformat(timespec="seconds"),
        }
        _save_pending(pending)

    return {"param": param, "old": old, "new": clamped, "changed": changed}


def has_pending_change() -> bool:
    return PENDING_FILE.exists()


def clear_pending() -> None:
    PENDING_FILE.unlink(missing_ok=True)


def enforce_rsi_gap(min_gap: float = _RSI_MIN_GAP) -> dict[str, Any] | None:
    """
    mr_rsi_oversold_fast < mr_rsi_oversold_slow 최소 간격을 보장.

    MeanReversionStrategy 생성자는 0 < fast < slow < exit 를 강제한다.
    자동 조정으로 두 값이 근접/역전되면 봇이 재시작 시 크래시하므로,
    간격이 min_gap 미만이면 fast를 slow - min_gap 으로 재클램프한다.
    """
    data = load()
    sp = data["strategy_params"]
    fast = float(sp["mr_rsi_oversold_fast"])
    slow = float(sp["mr_rsi_oversold_slow"])
    if slow - fast >= min_gap:
        return None

    new_fast = _clamp("mr_rsi_oversold_fast", slow - min_gap)
    if math.isclose(new_fast, fast, rel_tol=1e-6, abs_tol=1e-6):
        return None

    sp["mr_rsi_oversold_fast"] = new_fast
    save(data)
    _append_audit(
        trigger="safety_gap",
        param="mr_rsi_oversold_fast",
        old=fast,
        new=new_fast,
        reason=f"fast/slow 최소 간격({min_gap}) 유지",
    )
    return {"param": "mr_rsi_oversold_fast", "old": fast, "new": new_fast}


def request_restart(reason: str) -> None:
    RESTART_FLAG.write_text(
        json.dumps(
            {"reason": reason, "requested_at": datetime.now(KST).isoformat(timespec="seconds")},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def restart_requested() -> bool:
    return RESTART_FLAG.exists()


def read_restart_reason() -> str | None:
    if not RESTART_FLAG.exists():
        return None
    try:
        return json.loads(RESTART_FLAG.read_text(encoding="utf-8")).get("reason")
    except Exception:
        return None


def clear_restart_request() -> None:
    RESTART_FLAG.unlink(missing_ok=True)
