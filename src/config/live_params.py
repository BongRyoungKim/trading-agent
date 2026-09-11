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

# reports/ is volume-mounted and owned by the container's runtime user (see
# docker-compose.yml) — unlike _ROOT itself, which is baked into the image as
# root during build, so a plain "_ROOT / '.restart_requested'" path raised
# PermissionError the first time request_restart() actually ran in production
# (apply_pending() had never reached this line before a change cleared the
# performance gate). reports/ already holds the other pending/audit state
# files below, so this keeps all runtime-mutable state in one writable place.
RESTART_FLAG = _ROOT / "reports" / ".restart_requested"
# 자동튜너가 제안한 변경 후보 — src/backtest/performance_gate.py 검증을 통과
# (scripts/validate_params.py --apply)하기 전까지는 여기 머무를 뿐 .strategy_
# params.json 에는 절대 반영되지 않는다. propose_and_apply()와 달리 이쪽은
# 라이브에 아무 영향을 주지 않는 순수 제안 단계.
PENDING_FILE = _ROOT / "reports" / "pending_param_change.json"
# scripts/validate_params.py 가 백테스트 검증(무거운 연산, 라이브 서버에서는
# 돌리지 않음)을 마치면 그 결과(pass/fail + 지표 비교)를 여기 캐시해둔다.
# 대시보드는 이 파일만 읽으면 되고, 승인 버튼을 누를 때도 이 파일에
# passed=True 가 찍혀 있어야만 실제로 반영한다 — 검증을 안 거친 제안은
# 대시보드에서도 절대 적용될 수 없다.
VALIDATION_FILE = _ROOT / "reports" / "pending_param_validation.json"

# 파라미터 1회 변동폭 상한: 이전 값의 ±20%
MAX_STEP_FRACTION = 0.20

DEFAULTS: dict[str, dict[str, Any]] = {
    "strategy_params": {
        "adx_trend_threshold": 25.0,
        "mr_vol_mult": 2.0,
        "mr_rsi_oversold_fast": 20.0,
        "mr_rsi_oversold_slow": 30.0,
        "mr_rsi_exit": 55.0,
        "mr_rsi_exit_fast": 70.0,
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
    # 하한 60.0: mr_rsi_oversold_slow(38.0)보다 명확히 높게 유지하고 현재 라이브값
    # (70.0)보다 낮은 값으로의 급격한 축소를 방지. 상한 85.0: RSI5가 85를 넘는
    # 경우는 극단적 과매수라 사실상 SELL 신호를 죽이는 것과 같아 안전 상한으로 설정.
    "mr_rsi_exit_fast":     (60.0, 85.0),
    "sm_vol_mult":          (0.3, 3.0),
    "sm_adx_threshold":     (20.0, 35.0),
    "sl_floor_pct":         (2.0, 4.0),
    "sl_ceiling_pct":       (1.0, 2.0),
    "atr_multiplier":       (1.5, 4.0),
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


def load_pending() -> dict[str, Any]:
    """Public read accessor for the pending proposal — {} if none is staged."""
    return _load_pending()


def clear_pending() -> None:
    PENDING_FILE.unlink(missing_ok=True)


def save_validation_result(
    passed: bool, checks: list[dict[str, Any]], baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> None:
    """scripts/validate_params.py calls this right after running the
    performance gate, whether it passed or failed, so the dashboard can
    show the comparison without re-running the (heavy) backtest itself."""
    VALIDATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    VALIDATION_FILE.write_text(
        json.dumps(
            {
                "passed": passed,
                "checks": checks,
                "baseline_metrics": baseline_metrics,
                "candidate_metrics": candidate_metrics,
                "validated_at": datetime.now(KST).isoformat(timespec="seconds"),
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


def load_validation_result() -> dict[str, Any] | None:
    if not VALIDATION_FILE.exists():
        return None
    try:
        return json.loads(VALIDATION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_validation_result() -> None:
    VALIDATION_FILE.unlink(missing_ok=True)


def apply_pending(trigger_source: str = "validate_params.py --apply") -> list[str]:
    """
    Commits the currently-staged pending change live: propose_and_apply()
    (step-cap/clamp/audit-log, same as any other tunable) for every param
    in PENDING_FILE, then enforce_rsi_gap(), request a restart if anything
    actually changed, and clear both the pending and validation files.

    Callers (scripts/validate_params.py --apply, the dashboard's approve
    endpoint) are responsible for checking load_validation_result()["passed"]
    is True *before* calling this — this function does not re-check the
    gate itself, it only performs the write.

    Returns a list of "param old->new" summary strings (empty if nothing
    was actually changed, e.g. every proposed value was already at the
    current live value or a safety bound).
    """
    pending = _load_pending()
    changed_summaries: list[str] = []
    for section in ("strategy_params", "risk"):
        for param, target_value in pending.get(section, {}).items():
            result = propose_and_apply(
                param, float(target_value),
                trigger="performance_gate_approved", reason=trigger_source,
            )
            if result["changed"]:
                changed_summaries.append(f"{param} {result['old']}->{result['new']}")

    enforce_rsi_gap()
    if changed_summaries:
        request_restart(f"{trigger_source}: " + ", ".join(changed_summaries))
    clear_pending()
    clear_validation_result()
    return changed_summaries


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
