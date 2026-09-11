"""
향후 과제 자동 실행기 (ScheduledTaskRunner)

DailyReportGenerator가 매일 스케줄 실행 시 호출한다.
_pending_tasks()에 나열된 조건을 실제로 평가하고, 조건 충족 시 작업을 수행한다.

작업 목록:
  - param_review_20  : 20건 달성 → 심볼별 성과 분석 + 파라미터 권장 JSON 저장 (정보성, 자동적용 없음)
  - wr_alert         : WR < 40% (10건+) → mr_rsi_oversold_fast/slow↓, mr_vol_mult↑ 자동 적용
  - pnl_alert        : 누적 PnL < 0 (10건+) → tp_rr_multiplier↑ 자동 적용
  - sl_review        : SL 청산 비율 과다 (5건+) → sl_floor_pct↑ 자동 적용
  - walk_forward_50  : 50건 달성 → Walk-Forward 실행 트리거 파일 생성

wr_alert / pnl_alert / sl_review 는 src.config.live_params.propose_pending() 을
통해 조정안을 제안만 한다 — PARAM_BOUNDS 클램프와 ±20% 1회 변동폭 제한은
그대로 거치지만, reports/pending_param_change.json 에 쌓일 뿐 .strategy_
params.json 에는 반영되지 않는다. 실제 라이브 반영은 사람이
`scripts/validate_params.py --apply` 로 src/backtest/performance_gate.py
검증을 통과시켜야만 일어난다(2026-09 도입 — 그 전에는 여기서 바로
propose_and_apply()로 즉시 반영했으나, 백테스트 검증 없이 실거래 파라미터가
자동으로 바뀌는 게 위험하다고 판단해 이 단계를 추가했다).

상태 파일 reports/.task_state.json 으로 중복 실행 방지.
5건 단위로 wr_alert / pnl_alert / sl_review 는 재평가한다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from src.config import live_params as lp

KST = timezone(timedelta(hours=9))
REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"
STATE_FILE  = REPORTS_DIR / ".task_state.json"
REC_DIR     = REPORTS_DIR / "recommendations"

STRATEGY_DEPLOY_TS = 1775556000   # 2026-04-07 19:00 KST


@dataclass
class ActionResult:
    task_id:     str
    title:       str
    status:      str          # "executed" | "skipped" | "error"
    detail:      str
    output_path: str | None = field(default=None)

    def to_telegram(self) -> str:
        icon = "✅" if self.status == "executed" else ("⚠️" if self.status == "error" else "⏭")
        lines = [f"{icon} <b>{self.title}</b>", self.detail]
        if self.output_path:
            lines.append(f"📄 {Path(self.output_path).name}")
        return "\n".join(lines)


class ScheduledTaskRunner:
    """조건 기반 향후 과제 자동 실행."""

    def __init__(self, journal_path: str = "data/journal.db") -> None:
        self._journal_path = journal_path
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        REC_DIR.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, mr_stats: dict) -> list[ActionResult]:
        """조건을 평가하고 해당 작업을 실행. 결과 목록을 반환."""
        results: list[ActionResult] = []
        total     = mr_stats["total"]
        wr        = mr_stats["wr_pct"]
        pnl       = mr_stats["total_pnl"]
        sl_count  = mr_stats["sl_count"]
        sig_count = mr_stats["signal_count"]
        tp_count  = mr_stats["tp_count"]

        # ① 20건 파라미터 검토 (1회)
        if total >= 20 and not self._done("param_review_20"):
            results.append(self._run_param_review())
            self._mark("param_review_20")

        # ② WR < 40% 경보 (10건+, 5건마다 재평가)
        if total >= 10 and wr < 40:
            key = f"wr_alert_{total // 5}"
            if not self._done(key):
                results.append(self._run_wr_alert(wr, mr_stats))
                self._mark(key)

        # ③ 누적 PnL 음수 경보 (10건+, 5건마다 재평가)
        if total >= 10 and pnl < 0:
            key = f"pnl_alert_{total // 5}"
            if not self._done(key):
                results.append(self._run_pnl_alert(pnl, mr_stats))
                self._mark(key)

        # ④ SL 청산 비율 과다 (5건+, 5건마다 재평가)
        if total > 5 and sl_count > (sig_count + tp_count):
            key = f"sl_review_{total // 5}"
            if not self._done(key):
                results.append(self._run_sl_review(sl_count, sig_count, tp_count))
                self._mark(key)

        # ⑤ 50건 Walk-Forward 트리거 (1회)
        if total >= 50 and not self._done("walk_forward_50"):
            results.append(self._trigger_walk_forward())
            self._mark("walk_forward_50")

        self._save_state()
        return results

    # ── Task implementations ──────────────────────────────────────────────────

    def _run_param_review(self) -> ActionResult:
        """심볼별 성과 분석 + 파라미터 권장 JSON 저장."""
        try:
            conn = sqlite3.connect(self._journal_path)
            cur  = conn.cursor()
            cur.execute(
                """
                SELECT symbol,
                    COUNT(*) as total,
                    SUM(CASE WHEN CAST(pnl AS REAL)>0 THEN 1 ELSE 0 END) as wins,
                    SUM(CAST(pnl AS REAL)) as total_pnl,
                    SUM(CASE WHEN CAST(pnl AS REAL)>0  THEN CAST(pnl AS REAL) ELSE 0 END) as gross_win,
                    SUM(CASE WHEN CAST(pnl AS REAL)<=0 THEN ABS(CAST(pnl AS REAL)) ELSE 0 END) as gross_loss,
                    COUNT(CASE WHEN reason='stop_loss' THEN 1 END) as sl_cnt,
                    AVG(CAST(pnl AS REAL)) as avg_pnl
                FROM trades
                WHERE entry_time >= ?
                GROUP BY symbol
                """,
                (STRATEGY_DEPLOY_TS,),
            )
            rows = cur.fetchall()
            conn.close()

            analysis: list[dict] = []
            summaries: list[str] = []
            for sym, total, wins, t_pnl, gw, gl, sl_cnt, avg_pnl in rows:
                wr       = wins / total * 100 if total else 0
                pf       = gw / gl if gl else None
                sl_ratio = sl_cnt / total * 100 if total else 0

                if wr < 40:
                    rec = "rsi_oversold_fast 25→20, vol_mult 1.5→2.0 (진입 조건 강화)"
                elif sl_ratio > 60:
                    rec = "SL 클램프 상향: sl_ceiling 1.5%→2.0% (노이즈 SL 완화)"
                else:
                    rec = "현재 파라미터 유지"

                entry = {
                    "symbol":        sym,
                    "total":         total,
                    "wr_pct":        round(wr, 1),
                    "profit_factor": round(pf, 2) if pf is not None else None,
                    "avg_pnl":       round(avg_pnl or 0, 1),
                    "sl_ratio_pct":  round(sl_ratio, 1),
                    "recommendation": rec,
                }
                analysis.append(entry)
                summaries.append(f"{sym} WR {wr:.1f}% → {rec}")

            out = REC_DIR / f"{date.today()}-param-review.json"
            out.write_text(
                json.dumps({"date": str(date.today()), "analysis": analysis},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            detail = " | ".join(summaries) if summaries else "분석 데이터 없음"
            logger.info("Param review completed", path=str(out))
            return ActionResult("param_review_20", "20건 파라미터 검토", "executed", detail, str(out))
        except Exception as exc:
            logger.error("param_review failed", error=str(exc))
            return ActionResult("param_review_20", "20건 파라미터 검토", "error", str(exc))

    def _run_wr_alert(self, wr: float, stats: dict) -> ActionResult:
        """WR < 40% — 진입 조건 강화안을 제안 (RSI 과매도 기준↓, 거래량 기준↑).

        performance_gate 도입(2026-09) 이전에는 여기서 바로 라이브에 반영했다.
        지금은 제안만 reports/pending_param_change.json 에 쌓아두고, 사람이
        `scripts/validate_params.py --apply` 로 백테스트 검증을 통과시켜야만
        실제로 .strategy_params.json 에 반영된다 — 검증 없이 자동으로
        파라미터가 바뀌어 실거래에 영향을 주는 걸 막기 위함."""
        trigger = f"WR {wr:.1f}% < 40% (거래 {stats['total']}건)"
        proposed = [
            lp.propose_pending(
                "mr_rsi_oversold_fast",
                lp.get_current("mr_rsi_oversold_fast") * 0.8,
                trigger="wr_alert", reason=trigger,
            ),
            lp.propose_pending(
                "mr_rsi_oversold_slow",
                lp.get_current("mr_rsi_oversold_slow") * 0.8,
                trigger="wr_alert", reason=trigger,
            ),
            lp.propose_pending(
                "mr_vol_mult",
                lp.get_current("mr_vol_mult") * 1.2,
                trigger="wr_alert", reason=trigger,
            ),
        ]

        rec = {
            "date":             str(date.today()),
            "trigger":          trigger,
            "current_stats":    stats,
            "proposed_changes": proposed,
        }
        out = REC_DIR / f"{date.today()}-wr-alert.json"
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

        changed = [a for a in proposed if a["changed"]]
        if changed:
            summary = ", ".join(f"{a['param']} {a['old']}→{a['new']}" for a in changed)
            detail = (
                f"{trigger} — 진입 조건 강화안 제안(미적용): {summary}\n"
                f"검증 필요 — 대시보드(승인/거부 버튼) 또는 scripts/validate_params.py --apply"
            )
        else:
            detail = f"{trigger} — 파라미터가 이미 안전범위 경계라 제안할 조정 없음"
        logger.warning("Low WR alert triggered", wr=wr, proposed=proposed, path=str(out))
        return ActionResult("wr_alert", "승률 경보 — 진입 조건 강화안 제안", "executed", detail, str(out))

    def _run_pnl_alert(self, pnl: float, stats: dict) -> ActionResult:
        """누적 PnL 음수 — TP 목표 상향안을 제안 (더 큰 수익으로 손실 상쇄).

        wr_alert와 동일하게 즉시 적용하지 않고 제안만 쌓는다 — 위 docstring
        참조."""
        trigger = f"누적 PnL {pnl:+,.0f}원 < 0 (거래 {stats['total']}건)"
        result = lp.propose_pending(
            "tp_rr_multiplier",
            lp.get_current("tp_rr_multiplier") * 1.2,
            trigger="pnl_alert", reason=trigger,
        )
        rec = {
            "date":             str(date.today()),
            "trigger":          trigger,
            "current_stats":    stats,
            "proposed_changes": [result],
        }
        out = REC_DIR / f"{date.today()}-pnl-alert.json"
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

        if result["changed"]:
            detail = (
                f"{trigger} — TP RR 상향안 제안(미적용): {result['old']}→{result['new']}\n"
                f"검증 필요 — 대시보드(승인/거부 버튼) 또는 scripts/validate_params.py --apply"
            )
        else:
            detail = f"{trigger} — tp_rr_multiplier가 이미 안전범위 경계라 제안할 조정 없음"
        logger.warning("Negative PnL alert triggered", pnl=pnl, proposed=result, path=str(out))
        return ActionResult("pnl_alert", "PnL 경보 — TP 목표 상향안 제안", "executed", detail, str(out))

    def _run_sl_review(self, sl_count: int, sig_count: int, tp_count: int) -> ActionResult:
        """SL 청산 비율 과다 — SL 허용폭 완화안을 제안.

        wr_alert와 동일하게 즉시 적용하지 않고 제안만 쌓는다 — 클래스 상단
        docstring 참조."""
        exit_total = sl_count + sig_count + tp_count
        sl_ratio   = sl_count / exit_total * 100 if exit_total else 0
        trigger = f"SL청산 {sl_count}건 / 전체 {exit_total}건 ({sl_ratio:.1f}%)"
        result = lp.propose_pending(
            "sl_floor_pct",
            lp.get_current("sl_floor_pct") * 1.2,
            trigger="sl_review", reason=trigger,
        )
        rec = {
            "date":             str(date.today()),
            "trigger":          trigger,
            "sl_count":         sl_count,
            "signal_count":     sig_count,
            "tp_count":         tp_count,
            "proposed_changes": [result],
        }
        out = REC_DIR / f"{date.today()}-sl-review.json"
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

        if result["changed"]:
            detail = (
                f"SL 청산 {sl_ratio:.1f}% 과다 — SL 허용폭 완화안 제안(미적용): "
                f"{result['old']}%→{result['new']}%\n검증 필요 — 대시보드(승인/거부 버튼) 또는 scripts/validate_params.py --apply"
            )
        else:
            detail = f"SL 청산 {sl_ratio:.1f}% 과다 — sl_floor_pct가 이미 안전범위 경계라 제안할 조정 없음"
        logger.warning("High SL ratio alert", sl_ratio=sl_ratio, proposed=result, path=str(out))
        return ActionResult("sl_review", "SL 비율 경보 — SL 허용폭 완화안 제안", "executed", detail, str(out))

    def _trigger_walk_forward(self) -> ActionResult:
        """50건 달성 — Walk-Forward 실행 트리거 파일 생성."""
        trigger = {
            "date":    str(date.today()),
            "status":  "pending",
            "command": "python scripts/backtest_swing.py --walk-forward",
            "note":    "50건 달성. Walk-Forward 최적화 수동 실행 필요. 예상 소요: 30분.",
        }
        out = REC_DIR / f"{date.today()}-walk-forward-trigger.json"
        out.write_text(json.dumps(trigger, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Walk-Forward trigger file created", path=str(out))
        return ActionResult(
            "walk_forward_50",
            "50건 달성 — Walk-Forward 트리거",
            "executed",
            "50건 달성. Walk-Forward 최적화 준비 완료 — 수동 실행 대기.",
            str(out),
        )

    # ── State persistence ─────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_state(self) -> None:
        STATE_FILE.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _done(self, key: str) -> bool:
        return key in self._state

    def _mark(self, key: str) -> None:
        self._state[key] = str(datetime.now(KST).date())
