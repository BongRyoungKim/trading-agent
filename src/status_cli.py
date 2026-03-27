"""
Status CLI: read-only view of the trading agent's current state.

Reads directly from the SQLite databases — no running engine needed.

Usage:
    python -m src.status_cli                # show everything
    python -m src.status_cli portfolio      # cash + open positions
    python -m src.status_cli trades [-n N]  # last N completed trades (default 20)
    python -m src.status_cli stats          # win rate, profit factor, total PnL
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.portfolio.journal_store import SQLiteJournalStore
from src.portfolio.position_store import SQLitePositionStore


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_pnl(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:,.4f}"


def _fmt_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _divider(width: int = 60) -> str:
    return "─" * width


# ── Subcommands ───────────────────────────────────────────────────────────────

def cmd_portfolio(pos_db: Path) -> int:
    store = SQLitePositionStore(db_path=pos_db)
    positions = store.load_all()

    print(_divider())
    print("  OPEN POSITIONS")
    print(_divider())

    if not positions:
        print("  (no open positions)")
    else:
        header = f"  {'Symbol':<14} {'Side':<6} {'Amount':>12} {'Entry Price':>14} {'Stop Loss':>12} {'Take Profit':>12}"
        print(header)
        print("  " + "·" * (len(header) - 2))
        for sym, pos in sorted(positions.items()):
            sl = f"{float(pos.stop_loss):,.2f}" if pos.stop_loss is not None else "—"
            tp = f"{float(pos.take_profit):,.2f}" if pos.take_profit is not None else "—"
            print(
                f"  {sym:<14} {pos.side:<6} {float(pos.amount):>12.6f}"
                f" {float(pos.entry_price):>14,.2f} {sl:>12} {tp:>12}"
            )

    print(f"\n  Total open: {len(positions)}")
    print(_divider())
    return 0


def cmd_trades(journal_db: Path, n: int = 20) -> int:
    store = SQLiteJournalStore(db_path=journal_db)
    trades = store.load_all()
    recent = trades[-n:]

    print(_divider())
    print(f"  RECENT TRADES  (last {len(recent)} of {len(trades)} total)")
    print(_divider())

    if not recent:
        print("  (no completed trades)")
    else:
        header = f"  {'Symbol':<14} {'Side':<6} {'Exit Date':<20} {'PnL':>12} {'Reason':<12}"
        print(header)
        print("  " + "·" * (len(header) - 2))
        for t in recent:
            date_str = t.exit_time.strftime("%Y-%m-%d %H:%M")
            pnl_str = _fmt_pnl(float(t.pnl))
            print(f"  {t.symbol:<14} {t.side:<6} {date_str:<20} {pnl_str:>12} {t.reason:<12}")

    print(_divider())
    return 0


def cmd_stats(journal_db: Path) -> int:
    from src.portfolio.journal import TradeJournal
    store = SQLiteJournalStore(db_path=journal_db)
    journal = TradeJournal.from_store(store)
    s = journal.stats()

    print(_divider())
    print("  PERFORMANCE STATISTICS")
    print(_divider())
    print(f"  {'Total trades':<22}: {s['total_trades']}")
    print(f"  {'Wins / Losses':<22}: {s['wins']} / {s['losses']}")
    print(f"  {'Win rate':<22}: {s['win_rate_pct']:.2f}%")
    print(f"  {'Avg win':<22}: {_fmt_pnl(s['avg_win'])}")
    print(f"  {'Avg loss':<22}: {_fmt_pnl(-s['avg_loss'])}")
    pf = s["profit_factor"]
    pf_str = f"{pf:.4f}" if pf != float("inf") else "∞"
    print(f"  {'Profit factor':<22}: {pf_str}")
    print(f"  {'Total realized PnL':<22}: {_fmt_pnl(s['total_pnl'])}")
    print(_divider())
    return 0


def cmd_all(pos_db: Path, journal_db: Path, trades_n: int = 20) -> int:
    cmd_portfolio(pos_db)
    print()
    cmd_stats(journal_db)
    print()
    cmd_trades(journal_db, n=trades_n)
    return 0


# ── CLI entry point ───────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.status_cli",
        description="Trading Agent — Status CLI (read-only)",
    )
    p.add_argument(
        "--pos-db",
        default="data/positions.db",
        help="Path to positions SQLite database (default: data/positions.db)",
    )
    p.add_argument(
        "--journal-db",
        default="data/journal.db",
        help="Path to journal SQLite database (default: data/journal.db)",
    )

    sub = p.add_subparsers(dest="command")

    sub.add_parser("portfolio", help="Show open positions")

    trades_p = sub.add_parser("trades", help="Show recent completed trades")
    trades_p.add_argument(
        "-n", type=int, default=20,
        help="Number of most recent trades to show (default: 20)",
    )

    sub.add_parser("stats", help="Show performance statistics")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    pos_db = Path(args.pos_db)
    journal_db = Path(args.journal_db)

    if args.command == "portfolio":
        return cmd_portfolio(pos_db)
    if args.command == "trades":
        return cmd_trades(journal_db, n=args.n)
    if args.command == "stats":
        return cmd_stats(journal_db)

    # No subcommand → show everything
    return cmd_all(pos_db, journal_db)


if __name__ == "__main__":
    sys.exit(main())
