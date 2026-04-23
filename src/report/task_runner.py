"""
향후 과제 자동 실행기 (ScheduledTaskRunner)

DailyReportGenerator가 매일 스케줄 실행 시 호출한다.
_pending_tasks()에 나열된 조건을 실제로 평가하고, 조건 충족 시 작업을 수행한다.

작업 목록:
  - param_review_20  : 20건 달성 → 심볼별 성과 분석 + 파라미터 권장 JSON 저장
  - wr_alert         : WR < 40% (10건+) → 진입 조건 강화 권장 JSON 저장
  - pnl_alert        : 누적 PnL < 0 (10건+) → SL/TP 조정 권장 JSON 저장
  - sl_review        : SL 청산 비율 과다 (5건+) → SL 범위 완화 권장 JSON 저장
  - walk_forward_50  : 50건 달성 → Walk-Forward 실행 트리거 파일 생성

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
        """WR < 40% — 진입 조건 강화 권장 저장."""
        rec = {
            "date":          str(date.today()),
            "trigger":       f"WR {wr:.1f}% < 40% (거래 {stats['total']}건)",
            "current_stats": stats,
            "recommended_changes": [
                "rsi_oversold_fast: 25 → 20  (더 극단적 과매도만 진입)",
                "rsi_oversold_slow: 35 → 30  (중기 RSI 기준 강화)",
                "vol_mult: 1.5 → 2.0         (거래량 기준 강화)",
            ],
            "note": "start_agent.bat --rsi_oversold_fast 등 파라미터 수동 적용 필요. 10건 더 축적 후 재평가.",
        }
        out = REC_DIR / f"{date.today()}-wr-alert.json"
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        detail = f"WR {wr:.1f}% < 40% — 진입 조건 강화 권장 (rsi_oversold_fast↓, vol_mult↑)"
        logger.warning("Low WR alert triggered", wr=wr, path=str(out))
        return ActionResult("wr_alert", "승률 경보 — 파라미터 조정 권장", "executed", detail, str(out))

    def _run_pnl_alert(self, pnl: float, stats: dict) -> ActionResult:
        """누적 PnL 음수 — SL/TP 조정 권장 저장."""
        rec = {
            "date":          str(date.today()),
            "trigger":       f"누적 PnL {pnl:+,.0f}원 < 0 (거래 {stats['total']}건)",
            "current_stats": stats,
            "recommended_changes": [
                "SL 클램프 하한 완화: sl_floor 2.5%→3.0% (손절 공간 확보)",
                "또는 TP 목표 상향: RR 2:1 → 3:1",
                "또는 rsi_exit 하향: 60 → 55 (더 빠른 수익 실현)",
            ],
            "note": "SL 클램프는 engine.py _handle_buy_signal() 직접 수정. 10건 후 재평가.",
        }
        out = REC_DIR / f"{date.today()}-pnl-alert.json"
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        detail = f"누적 PnL {pnl:+,.0f}원 음수 — SL/TP 구조 재검토 권장"
        logger.warning("Negative PnL alert triggered", pnl=pnl, path=str(out))
        return ActionResult("pnl_alert", "PnL 경보 — SL/TP 조정 권장", "executed", detail, str(out))

    def _run_sl_review(self, sl_count: int, sig_count: int, tp_count: int) -> ActionResult:
        """SL 청산 비율 과다 — SL 범위 완화 권장 저장."""
        exit_total = sl_count + sig_count + tp_count
        sl_ratio   = sl_count / exit_total * 100 if exit_total else 0
        rec = {
            "date":          str(date.today()),
            "trigger":       f"SL청산 {sl_count}건 / 전체 {exit_total}건 ({sl_ratio:.1f}%)",
            "sl_count":      sl_count,
            "signal_count":  sig_count,
            "tp_count":      tp_count,
            "recommended_changes": [
                "SL 클램프 하한 완화: sl_floor 2.5%→3.0% (손절 공간 확보)",
                "또는 ATR multiplier 상향: 2.0→2.5 (변동성 대비 여유)",
                "또는 진입 기준 강화로 SL에 걸리는 진입 자체를 줄임",
            ],
        }
        out = REC_DIR / f"{date.today()}-sl-review.json"
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        detail = f"SL 청산 {sl_ratio:.1f}% 과다 ({sl_count}/{exit_total}건) — SL 범위 완화 권장"
        logger.warning("High SL ratio alert", sl_ratio=sl_ratio, path=str(out))
        return ActionResult("sl_review", "SL 비율 검토 — 범위 완화 권장", "executed", detail, str(out))

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
