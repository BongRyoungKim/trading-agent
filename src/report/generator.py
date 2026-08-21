"""
Daily Report Generator

저널 DB + 실시간 시장 데이터를 조합해 Markdown 보고서를 생성하고
reports/YYYY-MM-DD.md 로 저장한다.

사용 방법:
    from src.report.generator import DailyReportGenerator
    gen = DailyReportGenerator(journal_path="data/journal.db")
    path = gen.generate()          # 오늘 날짜 보고서
    path = gen.generate("2026-04-08")  # 특정 날짜
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from src.report.task_runner import ActionResult, ScheduledTaskRunner

PARAM_CHANGE_LOG = Path(__file__).parent.parent.parent / "reports" / "param_change_log.jsonl"

KST = timezone(timedelta(hours=9))
REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"

# RegimeAdaptiveStrategy 배포 시점 (KST Unix ts)
STRATEGY_DEPLOY_TS = 1780585200   # 2026-06-05 00:00 KST


class DailyReportGenerator:
    """일일 보고서 생성기."""

    def __init__(
        self,
        journal_path: str = "data/journal.db",
        symbols: list[str] | None = None,
        run_tasks: bool = False,
    ) -> None:
        self._journal_path = journal_path
        self._symbols = symbols or ["LINK/KRW", "BTC/KRW", "XRP/KRW"]
        self._run_tasks = run_tasks
        self.last_task_results: list[ActionResult] = []
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def generate(self, target_date: str | None = None) -> Path:
        """
        보고서 생성 및 저장.

        Args:
            target_date: "YYYY-MM-DD" 형식. None이면 오늘 KST.

        Returns:
            저장된 파일 경로.
        """
        if target_date:
            report_date = date.fromisoformat(target_date)
        else:
            report_date = datetime.now(KST).date()

        logger.info("Generating daily report", date=str(report_date))

        market = self._fetch_market_status()
        prev_trades = self._query_day_trades(report_date - timedelta(days=1))
        today_trades = self._query_day_trades(report_date)
        mr_stats = self._query_strategy_stats(STRATEGY_DEPLOY_TS)
        all_stats = self._query_all_stats()
        tasks = self._pending_tasks(mr_stats)

        # 향후 과제 자동 실행 (run_tasks=True 일 때만)
        self.last_task_results = []
        if self._run_tasks:
            try:
                runner = ScheduledTaskRunner(journal_path=self._journal_path)
                self.last_task_results = runner.run(mr_stats)
                logger.info(
                    "Scheduled tasks executed",
                    count=len(self.last_task_results),
                    executed=sum(1 for r in self.last_task_results if r.status == "executed"),
                )
            except Exception as exc:
                logger.error("Scheduled task runner failed", error=str(exc))

        md = self._render(
            report_date=report_date,
            market=market,
            prev_trades=prev_trades,
            today_trades=today_trades,
            mr_stats=mr_stats,
            all_stats=all_stats,
            tasks=tasks,
            task_results=self.last_task_results,
        )

        out_path = REPORTS_DIR / f"{report_date}.md"
        out_path.write_text(md, encoding="utf-8")
        logger.info("Daily report saved", path=str(out_path))
        return out_path

    def generate_weekly(self) -> tuple[Path, str]:
        """
        최근 7일 실적 + 자동 파라미터 조정 내역을 담은 주간 보고서 생성.

        Returns:
            (저장된 Markdown 경로, Telegram 발송용 요약 텍스트)
        """
        week_end = datetime.now(KST).date()
        week_start = week_end - timedelta(days=7)
        week_start_ts = datetime(
            week_start.year, week_start.month, week_start.day, 0, 0, 0, tzinfo=KST
        ).timestamp()
        week_end_ts = datetime(
            week_end.year, week_end.month, week_end.day, 0, 0, 0, tzinfo=KST
        ).timestamp() + 86400

        conn = sqlite3.connect(self._journal_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN CAST(pnl AS REAL)>0 THEN 1 ELSE 0 END),
                   SUM(CAST(pnl AS REAL)),
                   COUNT(CASE WHEN reason='stop_loss' THEN 1 END),
                   COUNT(CASE WHEN reason='take_profit' THEN 1 END),
                   COUNT(CASE WHEN reason='signal' THEN 1 END)
            FROM trades WHERE exit_time >= ? AND exit_time < ?
            """,
            (week_start_ts, week_end_ts),
        )
        total, wins, week_pnl, sl_cnt, tp_cnt, sig_cnt = cur.fetchone()
        conn.close()
        total = total or 0
        wins = wins or 0
        week_pnl = week_pnl or 0.0

        mr_stats = self._query_strategy_stats(STRATEGY_DEPLOY_TS)
        all_stats = self._query_all_stats()
        param_changes = self._recent_param_changes(since_ts=week_start_ts)

        md = self._render_weekly(
            week_start=week_start, week_end=week_end,
            total=total, wins=wins, week_pnl=week_pnl,
            sl_cnt=sl_cnt or 0, tp_cnt=tp_cnt or 0, sig_cnt=sig_cnt or 0,
            mr_stats=mr_stats, all_stats=all_stats, param_changes=param_changes,
        )

        out_dir = REPORTS_DIR / "weekly"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{week_end}-weekly.md"
        out_path.write_text(md, encoding="utf-8")
        logger.info("Weekly report saved", path=str(out_path))

        summary = self._render_weekly_telegram(
            week_start, week_end, total, wins, week_pnl, mr_stats, param_changes
        )
        return out_path, summary

    def _recent_param_changes(self, since_ts: float) -> list[dict]:
        """reports/param_change_log.jsonl 에서 since_ts 이후 항목만 필터링."""
        if not PARAM_CHANGE_LOG.exists():
            return []
        since_dt = datetime.fromtimestamp(since_ts, tz=KST)
        changes: list[dict] = []
        for line in PARAM_CHANGE_LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entry_dt = datetime.fromisoformat(entry["date"])
                if entry_dt >= since_dt:
                    changes.append(entry)
            except Exception:
                continue
        return changes

    # ── Data queries ──────────────────────────────────────────────────────────

    def _fetch_market_status(self) -> list[dict]:
        """ccxt로 현재 시장 상태 조회 (실패 시 빈 리스트)."""
        try:
            import ccxt  # noqa: PLC0415
            from src.utils.indicators import rsi as calc_rsi  # noqa: PLC0415
            import pandas as pd  # noqa: PLC0415

            exchange = ccxt.upbit({"enableRateLimit": True})
            rows = []
            for sym in self._symbols:
                try:
                    ohlcv = exchange.fetch_ohlcv(sym, "15m", limit=50)
                    df = pd.DataFrame(
                        ohlcv, columns=["ts", "open", "high", "low", "close", "volume"]
                    ).astype(float)
                    close = df["close"]
                    vol = df["volume"]
                    rsi14 = float(calc_rsi(close, 14).iloc[-1])
                    rsi5  = float(calc_rsi(close, 5).iloc[-1])
                    vol_ratio = float(vol.iloc[-1] / vol.rolling(20).mean().iloc[-1])
                    price = float(close.iloc[-1])
                    rows.append({
                        "symbol":    sym,
                        "price":     price,
                        "rsi14":     round(rsi14, 1),
                        "rsi5":      round(rsi5, 1),
                        "vol_ratio": round(vol_ratio, 2),
                    })
                except Exception as exc:
                    logger.debug(f"Market fetch failed for {sym}: {exc}")
                    rows.append({"symbol": sym, "price": None, "rsi14": None, "rsi5": None, "vol_ratio": None})
            return rows
        except Exception as exc:
            logger.warning(f"Market status fetch failed: {exc}")
            return []

    def _query_day_trades(self, day: date) -> list[dict]:
        """특정 날(KST) 거래 목록."""
        # KST 00:00 ~ 23:59 → UTC -9h
        day_start_ts = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=KST).timestamp()
        day_end_ts   = day_start_ts + 86400
        conn = sqlite3.connect(self._journal_path)
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT symbol, CAST(entry_price AS REAL), CAST(exit_price AS REAL),
                   CAST(pnl AS REAL), reason,
                   datetime(entry_time,'unixepoch','+9 hours'),
                   datetime(exit_time,'unixepoch','+9 hours')
            FROM trades
            WHERE exit_time >= ? AND exit_time < ?
            ORDER BY exit_time
            """,
            (day_start_ts, day_end_ts),
        )
        rows = [
            {
                "symbol":      r[0],
                "entry_price": r[1],
                "exit_price":  r[2],
                "pnl":         r[3] or 0.0,
                "reason":      r[4],
                "entry_kst":   r[5],
                "exit_kst":    r[6],
            }
            for r in cur.fetchall()
        ]
        conn.close()
        return rows

    def _query_strategy_stats(self, since_ts: float) -> dict:
        """현재 전략(RegimeAdaptiveStrategy) 배포 이후 통계."""
        conn = sqlite3.connect(self._journal_path)
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN CAST(pnl AS REAL)>0 THEN 1 ELSE 0 END) as wins,
                SUM(CAST(pnl AS REAL)) as total_pnl,
                AVG(CAST(pnl AS REAL)) as avg_pnl,
                SUM(CASE WHEN CAST(pnl AS REAL)>0 THEN CAST(pnl AS REAL) ELSE 0 END) as gross_win,
                SUM(CASE WHEN CAST(pnl AS REAL)<=0 THEN ABS(CAST(pnl AS REAL)) ELSE 0 END) as gross_loss,
                COUNT(CASE WHEN reason='stop_loss' THEN 1 END) as sl_count,
                COUNT(CASE WHEN reason='take_profit' THEN 1 END) as tp_count,
                COUNT(CASE WHEN reason='signal' THEN 1 END) as signal_count
            FROM trades WHERE entry_time >= ?
            """,
            (since_ts,),
        )
        r = cur.fetchone()
        conn.close()
        total, wins, total_pnl, avg_pnl, gross_win, gross_loss, sl_cnt, tp_cnt, sig_cnt = r
        total = total or 0
        wins  = wins or 0
        gross_win  = gross_win or 0
        gross_loss = gross_loss or 0
        pf = gross_win / gross_loss if gross_loss else float("inf")
        return {
            "total":        total,
            "wins":         wins,
            "losses":       total - wins,
            "wr_pct":       wins / total * 100 if total else 0,
            "total_pnl":    total_pnl or 0,
            "avg_pnl":      avg_pnl or 0,
            "profit_factor": round(pf, 2) if pf != float("inf") else None,
            "sl_count":     sl_cnt or 0,
            "tp_count":     tp_cnt or 0,
            "signal_count": sig_cnt or 0,
        }

    def _query_all_stats(self) -> dict:
        """전체 저널 누적 통계."""
        conn = sqlite3.connect(self._journal_path)
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*), SUM(CAST(pnl AS REAL)),
                   SUM(CASE WHEN CAST(pnl AS REAL)>0 THEN 1 ELSE 0 END)
            FROM trades
            """
        )
        total, total_pnl, wins = cur.fetchone()
        conn.close()
        total = total or 0
        wins  = wins or 0
        return {
            "total":     total,
            "wins":      wins,
            "losses":    total - wins,
            "wr_pct":    wins / total * 100 if total else 0,
            "total_pnl": total_pnl or 0,
        }

    # ── Pending tasks logic ───────────────────────────────────────────────────

    def _pending_tasks(self, mr_stats: dict) -> list[str]:
        tasks = []
        total = mr_stats["total"]
        wr    = mr_stats["wr_pct"]
        pnl   = mr_stats["total_pnl"]

        if total < 20:
            tasks.append(f"실거래 데이터 축적 중 ({total}/20건) — 20건 이후 파라미터 재검토")
        elif total < 50:
            tasks.append(f"실거래 {total}건 — 50건 달성 후 Walk-Forward 최적화 실행")

        if total >= 10 and wr < 40:
            tasks.append(f"승률 {wr:.1f}% < 40% — 전략 파라미터 재검토 또는 국면 감지 임계값 조정 필요")

        if total >= 10 and pnl < 0:
            tasks.append("누적 PnL 음수 — 손절 기준(SL %) 재조정 또는 진입 조건 강화 검토")

        if mr_stats["sl_count"] > mr_stats["signal_count"] + mr_stats["tp_count"]:
            tasks.append("SL 청산 비율 높음 — 보유 시간 또는 SL 범위 재검토")

        tasks.append("페이퍼 모드 유지 — 50건 이상 & WR 45%+ 달성 시 실거래 전환 검토")
        return tasks

    # ── Markdown renderer ─────────────────────────────────────────────────────

    def _render(
        self,
        report_date: date,
        market: list[dict],
        prev_trades: list[dict],
        today_trades: list[dict],
        mr_stats: dict,
        all_stats: dict,
        tasks: list[str],
        task_results: list[ActionResult] | None = None,
    ) -> str:
        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
        prev_date = report_date - timedelta(days=1)

        lines: list[str] = [
            f"# 트레이딩 일일 보고서 — {report_date}",
            f"\n> 생성: {now_kst}  |  전략: RegimeAdaptiveStrategy  |  모드: Live",
            "",
            "---",
            "",
        ]

        # 1. 시장 현황
        lines += [
            "## 1. 시장 현황",
            "",
            "| 심볼 | 가격(KRW) | RSI14 | RSI5 | Vol비율 | 신호 |",
            "|------|----------:|------:|-----:|--------:|------|",
        ]
        for m in market:
            if m["price"] is None:
                lines.append(f"| {m['symbol']} | — | — | — | — | — |")
                continue
            rsi14 = m["rsi14"]
            rsi5  = m["rsi5"]
            if rsi5 is not None and rsi5 < 25 and rsi14 is not None and rsi14 < 35:
                sig = "**BUY 대기**"
            elif rsi14 is not None and rsi14 >= 60:
                sig = "SELL 대기"
            else:
                sig = "HOLD"
            lines.append(
                f"| {m['symbol']} | {m['price']:>13,.0f} | {rsi14} | {rsi5} "
                f"| {m['vol_ratio']}× | {sig} |"
            )
        lines.append("")

        # 2. 전일 실적
        lines += [
            f"## 2. 전일 실적 ({prev_date})",
            "",
        ]
        if prev_trades:
            lines += [
                "| 심볼 | 진입가 | 청산가 | PnL(원) | 사유 | 진입(KST) | 청산(KST) |",
                "|------|-------:|-------:|--------:|------|-----------|-----------|",
            ]
            day_pnl = 0.0
            day_wins = 0
            for t in prev_trades:
                pnl_str = f"{t['pnl']:+,.0f}"
                day_pnl += t["pnl"]
                if t["pnl"] > 0:
                    day_wins += 1
                ep = f"{t['entry_price']:,.0f}" if t["entry_price"] else "—"
                xp = f"{t["exit_price"]:,.0f}" if t["exit_price"] else "—"
                et = t["entry_kst"][5:16] if t["entry_kst"] else "—"
                xt = t["exit_kst"][5:16] if t["exit_kst"] else "—"
                lines.append(f"| {t['symbol']} | {ep} | {xp} | {pnl_str} | {t['reason']} | {et} | {xt} |")
            wr_d = day_wins / len(prev_trades) * 100
            lines += [
                "",
                f"**전일 요약**: {len(prev_trades)}건 | 승 {day_wins} / 패 {len(prev_trades)-day_wins} "
                f"| WR {wr_d:.1f}% | 일간 PnL **{day_pnl:+,.0f}원**",
            ]
        else:
            lines.append(f"전일({prev_date}) 거래 없음.")
        lines.append("")

        # 2b. 당일 거래 (오늘)
        if today_trades:
            lines += [
                f"## 2b. 당일 거래 ({report_date}, 현재까지)",
                "",
                "| 심볼 | 진입가 | 청산가 | PnL(원) | 사유 |",
                "|------|-------:|-------:|--------:|------|",
            ]
            today_pnl = sum(t["pnl"] for t in today_trades)
            for t in today_trades:
                ep = f"{t['entry_price']:,.0f}" if t["entry_price"] else "—"
                xp = f"{t['exit_price']:,.0f}" if t["exit_price"] else "—"
                lines.append(f"| {t['symbol']} | {ep} | {xp} | {t['pnl']:+,.0f} | {t['reason']} |")
            lines += ["", f"**당일 PnL**: {today_pnl:+,.0f}원", ""]

        # 3. 누적 성과 (RegimeAdaptiveStrategy)
        s = mr_stats
        pf_str = f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "∞"
        lines += [
            "## 3. 누적 성과 (RegimeAdaptiveStrategy 이후)",
            "",
            f"| 항목 | 값 |",
            f"|------|----|",
            f"| 총 거래 | {s['total']}건 |",
            f"| 승 / 패 | {s['wins']} / {s['losses']} |",
            f"| 승률 | {s['wr_pct']:.1f}% |",
            f"| 누적 PnL | {s['total_pnl']:+,.0f}원 |",
            f"| 평균 EV | {s['avg_pnl']:+,.0f}원/거래 |",
            f"| Profit Factor | {pf_str} |",
            f"| SL청산 / TP청산 / Signal청산 | {s['sl_count']} / {s['tp_count']} / {s['signal_count']} |",
            "",
        ]

        # 전체 저널 (참고)
        a = all_stats
        lines += [
            "### 전체 저널 누적 (모든 전략 포함, 참고용)",
            "",
            f"총 {a['total']}건 | 승 {a['wins']} / 패 {a['losses']} "
            f"| WR {a['wr_pct']:.1f}% | 총 PnL {a['total_pnl']:+,.0f}원",
            "",
        ]

        # 4. 향후 과제
        lines += [
            "## 4. 향후 과제",
            "",
        ]
        for task in tasks:
            lines.append(f"- [ ] {task}")
        lines.append("")

        # 5. 비고
        lines += [
            "## 5. 비고",
            "",
            "| 항목 | 내용 |",
            "|------|------|",
            "| 전략 | RegimeAdaptiveStrategy (ADX+EMA 국면 자동 전환) |",
            "| 전략 배포 | 2026-06-05 KST |",
            "| 국면 전환 기준 | ADX≥25 + EMA20>EMA50 → SwingMomentum / 나머지 → MeanReversion |",
            "| SL 설정 | ATR×2.0, [1.5%~2.5%] 클램프 |",
            "| TP 설정 | SL × 1.5 (1.5:1 RR) |",
            "| Trailing Stop | 1.5% |",
            "| 모드 | Live (Upbit 실거래) |",
            "",
        ]

        # 6. 자동 실행 작업 결과
        if task_results:
            executed = [r for r in task_results if r.status == "executed"]
            errors   = [r for r in task_results if r.status == "error"]
            lines += [
                "## 6. 자동 실행 작업 결과",
                "",
            ]
            if executed:
                lines.append(f"총 {len(executed)}건 실행 완료:")
                lines.append("")
                for r in executed:
                    lines.append(f"### ✅ {r.title}")
                    lines.append(f"> {r.detail}")
                    if r.output_path:
                        lines.append(f"> 📄 저장: `{Path(r.output_path).name}`")
                    lines.append("")
            if errors:
                lines.append(f"⚠️ 오류 {len(errors)}건:")
                for r in errors:
                    lines.append(f"- **{r.title}**: {r.detail}")
                lines.append("")
        else:
            lines += [
                "## 6. 자동 실행 작업 결과",
                "",
                "조건 미충족 — 실행된 작업 없음.",
                "",
            ]

        lines += [
            "---",
            f"*자동 생성: {now_kst}*",
        ]

        return "\n".join(lines)

    # ── Weekly renderer ───────────────────────────────────────────────────────

    def _render_weekly(
        self,
        week_start: date,
        week_end: date,
        total: int,
        wins: int,
        week_pnl: float,
        sl_cnt: int,
        tp_cnt: int,
        sig_cnt: int,
        mr_stats: dict,
        all_stats: dict,
        param_changes: list[dict],
    ) -> str:
        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
        wr = wins / total * 100 if total else 0
        s = mr_stats
        pf_str = f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "∞"

        lines: list[str] = [
            f"# 트레이딩 주간 보고서 — {week_start} ~ {week_end}",
            f"\n> 생성: {now_kst}  |  전략: RegimeAdaptiveStrategy  |  모드: Live",
            "",
            "---",
            "",
            "## 1. 이번 주 실적",
            "",
            "| 항목 | 값 |",
            "|------|----|",
            f"| 거래 | {total}건 |",
            f"| 승 / 패 | {wins} / {total - wins} |",
            f"| 승률 | {wr:.1f}% |",
            f"| 주간 PnL | {week_pnl:+,.0f}원 |",
            f"| SL / TP / Signal 청산 | {sl_cnt} / {tp_cnt} / {sig_cnt} |",
            "",
            "## 2. 누적 성과 (RegimeAdaptiveStrategy 이후)",
            "",
            "| 항목 | 값 |",
            "|------|----|",
            f"| 총 거래 | {s['total']}건 |",
            f"| 승률 | {s['wr_pct']:.1f}% |",
            f"| 누적 PnL | {s['total_pnl']:+,.0f}원 |",
            f"| Profit Factor | {pf_str} |",
            "",
            "### 전체 저널 누적 (모든 전략 포함, 참고용)",
            "",
            f"총 {all_stats['total']}건 | WR {all_stats['wr_pct']:.1f}% "
            f"| 총 PnL {all_stats['total_pnl']:+,.0f}원",
            "",
            "## 3. 이번 주 자동 파라미터 조정 내역",
            "",
        ]

        if param_changes:
            lines += [
                "| 일시 | 트리거 | 파라미터 | 이전 | 이후 | 사유 |",
                "|------|--------|----------|-----:|-----:|------|",
            ]
            for c in param_changes:
                lines.append(
                    f"| {c['date'][:16]} | {c['trigger']} | {c['param']} "
                    f"| {c['old']} | {c['new']} | {c['reason']} |"
                )
        else:
            lines.append("이번 주 자동 조정 없음.")
        lines.append("")

        lines += [
            "---",
            f"*자동 생성: {now_kst}*",
        ]
        return "\n".join(lines)

    def _render_weekly_telegram(
        self,
        week_start: date,
        week_end: date,
        total: int,
        wins: int,
        week_pnl: float,
        mr_stats: dict,
        param_changes: list[dict],
    ) -> str:
        wr = wins / total * 100 if total else 0
        lines = [
            f"📊 <b>주간 리포트 ({week_start} ~ {week_end})</b>",
            f"이번 주: {total}건 | WR {wr:.1f}% | PnL {week_pnl:+,.0f}원",
            f"누적(현 전략): {mr_stats['total']}건 | WR {mr_stats['wr_pct']:.1f}% "
            f"| PnL {mr_stats['total_pnl']:+,.0f}원",
        ]
        if param_changes:
            lines.append(f"\n⚙️ 이번 주 자동 조정 {len(param_changes)}건:")
            for c in param_changes[-5:]:
                lines.append(f"  • {c['param']}: {c['old']}→{c['new']} ({c['trigger']})")
        else:
            lines.append("\n이번 주 자동 파라미터 조정 없음.")
        return "\n".join(lines)
