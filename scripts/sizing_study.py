"""
Position-sizing study: replay the live trade journal under alternative
position_size_pct values.

Read-only. Point --db at a LOCAL COPY of the production journal; never run
this against the server's live file:

    scp -i ./ssh-key-2026-08-25.key ubuntu@<host>:~/trading-agent/data/journal.db /tmp/journal_live.db
    python scripts/sizing_study.py --db /tmp/journal_live.db

The journal is segmented by live parameter-change timestamps (see
reports/param_change_log.jsonl on the server) so that each reported segment
comes from a single homogeneous parameter regime.

Metrics are the sizing-sensitive set from src/backtest/portfolio_metrics.py —
profit factor / win rate / per-trade Sharpe are deliberately absent because
they do not change with position size.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtest.portfolio_metrics import (  # noqa: E402
    bootstrap_sizing_paths,
    equity_max_drawdown_pct,
    worst_loss_streak,
)
from src.backtest.sizing_replay import (  # noqa: E402
    ReplayTrade,
    SizingConfig,
    SkipReason,
    replay_with_sizing,
    to_replay_trades,
)

DEFAULT_GRID = (0.10, 0.15, 0.25, 0.35, 0.50, 0.75, 1.00)


@dataclass(frozen=True)
class Segment:
    name: str
    trades: tuple[ReplayTrade, ...]


def load_trades(db_path: str) -> list[ReplayTrade]:
    """Read every completed trade from a journal.db copy (immutable, no writes)."""
    con = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT symbol, amount, entry_price, entry_time, exit_time, pnl "
            "FROM trades ORDER BY entry_time ASC"
        ).fetchall()
    finally:
        con.close()
    return to_replay_trades([dict(r) for r in rows])


def build_segments(
    trades: list[ReplayTrade], change_start: datetime | None, change_end: datetime | None
) -> list[Segment]:
    """Split into pre-change / post-change regimes plus the full sample."""
    segments = [Segment("ALL (mixed params)", tuple(trades))]
    if change_start is None or change_end is None:
        return segments
    before = tuple(t for t in trades if t.entry_time < change_start)
    during = tuple(t for t in trades if change_start <= t.entry_time <= change_end)
    after = tuple(t for t in trades if t.entry_time > change_end)
    segments.append(Segment(f"A pre-change  (< {change_start:%Y-%m-%d %H:%M}Z)", before))
    if during:
        segments.append(Segment("T during-change", during))
    segments.append(Segment(f"B post-change (> {change_end:%Y-%m-%d %H:%M}Z)", after))
    return segments


def _describe(segment: Segment) -> str:
    if not segment.trades:
        return f"{segment.name}: n=0"
    rets = [t.return_pct for t in segment.trades]
    wins = sum(1 for r in rets if r > 0)
    span_days = (
        max(t.exit_time for t in segment.trades) - min(t.entry_time for t in segment.trades)
    ).total_seconds() / 86400
    return (
        f"{segment.name}: n={len(rets)} days={span_days:.1f} "
        f"win={wins}/{len(rets)} mean_ret={sum(rets) / len(rets) * 100:+.3f}%"
    )


def run_grid(
    segment: Segment,
    capital: Decimal,
    grid: tuple[float, ...],
    max_slots: int,
    n_paths: int,
    seed: int,
    ruin_pct: float,
) -> None:
    print(f"\n{_describe(segment)}")
    if not segment.trades:
        return
    header = (
        f"{'size%':>6} {'final KRW':>12} {'ret%':>8} {'MDD%':>7} "
        f"{'streak':>8} {'streak%':>8} {'skip':>5} | "
        f"{'boot p5%':>9} {'boot med%':>10} {'boot p95%':>10} {'bootMDD95%':>11} {'P(ruin)':>8}"
    )
    print(header)
    print("-" * len(header))
    returns = [t.return_pct for t in segment.trades]
    for pct in grid:
        config = SizingConfig(
            initial_capital=capital,
            position_size_pct=pct,
            max_open_positions=max_slots,
        )
        result = replay_with_sizing(segment.trades, config)
        mdd = equity_max_drawdown_pct([p.equity for p in result.equity_points])
        streak = worst_loss_streak(
            [e.pnl for e in result.executed], equity_at_streak_start=capital
        )
        boot = bootstrap_sizing_paths(
            returns, position_size_pct=pct, n_paths=n_paths, seed=seed,
            ruin_threshold_pct=ruin_pct,
        )
        slot_skips = sum(1 for s in result.skipped if s.reason is SkipReason.SLOT_LIMIT)
        cash_skips = sum(1 for s in result.skipped if s.reason is SkipReason.INSUFFICIENT_CASH)
        print(
            f"{pct * 100:5.0f}% {float(result.final_capital):12,.0f} "
            f"{result.total_return_pct:+7.2f} {mdd:7.2f} "
            f"{streak.length:8d} {streak.decline_pct:8.2f} "
            f"{slot_skips:2d}/{cash_skips:<2d} | "
            f"{boot.final_return_p5:+9.2f} {boot.final_return_median:+10.2f} "
            f"{boot.final_return_p95:+10.2f} {boot.max_drawdown_p95:11.2f} "
            f"{boot.prob_ruin:8.3f}"
        )


def _parse_utc(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(UTC) if value else None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 guard
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, help="path to a LOCAL COPY of journal.db")
    p.add_argument("--initial-capital", type=Decimal, default=Decimal("1700000"))
    p.add_argument("--max-open-positions", type=int, default=4)
    p.add_argument("--change-start", default="2026-09-11T15:24:28+09:00",
                   help="first live param change in the sample (ISO-8601)")
    p.add_argument("--change-end", default="2026-09-11T16:43:23+09:00",
                   help="last live param change in the sample (ISO-8601)")
    p.add_argument("--grid", default=",".join(str(g) for g in DEFAULT_GRID))
    p.add_argument("--paths", type=int, default=5000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--ruin-pct", type=float, default=20.0)
    args = p.parse_args()

    trades = load_trades(args.db)
    grid = tuple(float(x) for x in args.grid.split(","))
    print(f"journal: {args.db} | trades={len(trades)} | "
          f"initial_capital={args.initial_capital:,} KRW | slots={args.max_open_positions}")
    print(f"bootstrap: {args.paths} paths, seed={args.seed}, ruin threshold={args.ruin_pct}%")

    for segment in build_segments(
        trades, _parse_utc(args.change_start), _parse_utc(args.change_end)
    ):
        run_grid(segment, args.initial_capital, grid, args.max_open_positions,
                 args.paths, args.seed, args.ruin_pct)


if __name__ == "__main__":
    main()
