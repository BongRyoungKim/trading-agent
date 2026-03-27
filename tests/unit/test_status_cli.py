"""Unit tests for src/status_cli.py."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.portfolio.journal import TradeRecord
from src.portfolio.journal_store import SQLiteJournalStore
from src.portfolio.models import Position
from src.portfolio.position_store import SQLitePositionStore
from src.status_cli import (
    _build_parser,
    cmd_all,
    cmd_portfolio,
    cmd_stats,
    cmd_trades,
    main,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

_T0 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
_T1 = datetime(2024, 1, 2, 12, 0, tzinfo=UTC)


def _pos(symbol: str = "BTC/USDT", entry: float = 50000.0,
         stop_loss: float | None = 45000.0) -> Position:
    return Position(
        symbol=symbol,
        side="buy",
        amount=Decimal("0.01"),
        entry_price=Decimal(str(entry)),
        entry_time=_T0,
        stop_loss=Decimal(str(stop_loss)) if stop_loss is not None else None,
    )


def _trade(symbol: str = "BTC/USDT", pnl: float = 100.0,
           reason: str = "signal") -> TradeRecord:
    return TradeRecord(
        symbol=symbol,
        side="buy",
        amount=Decimal("0.01"),
        entry_price=Decimal("50000"),
        exit_price=Decimal("51000"),
        entry_time=_T0,
        exit_time=_T1,
        pnl=Decimal(str(pnl)),
        commission=Decimal("0"),
        reason=reason,
    )


@pytest.fixture()
def pos_db(tmp_path: Path) -> Path:
    return tmp_path / "positions.db"


@pytest.fixture()
def journal_db(tmp_path: Path) -> Path:
    return tmp_path / "journal.db"


# ── Parser ────────────────────────────────────────────────────────────────────

class TestBuildParser:
    def test_defaults(self):
        args = _build_parser().parse_args([])
        assert args.pos_db == "data/positions.db"
        assert args.journal_db == "data/journal.db"
        assert args.command is None

    def test_portfolio_subcommand(self):
        args = _build_parser().parse_args(["portfolio"])
        assert args.command == "portfolio"

    def test_trades_subcommand_default_n(self):
        args = _build_parser().parse_args(["trades"])
        assert args.command == "trades"
        assert args.n == 20

    def test_trades_subcommand_custom_n(self):
        args = _build_parser().parse_args(["trades", "-n", "5"])
        assert args.n == 5

    def test_stats_subcommand(self):
        args = _build_parser().parse_args(["stats"])
        assert args.command == "stats"

    def test_custom_db_paths(self):
        args = _build_parser().parse_args(["--pos-db", "/tmp/p.db", "--journal-db", "/tmp/j.db"])
        assert args.pos_db == "/tmp/p.db"
        assert args.journal_db == "/tmp/j.db"


# ── cmd_portfolio ─────────────────────────────────────────────────────────────

class TestCmdPortfolio:
    def test_empty_positions_message(self, pos_db, capsys):
        SQLitePositionStore(db_path=pos_db)  # empty store
        rc = cmd_portfolio(pos_db)
        assert rc == 0
        assert "no open positions" in capsys.readouterr().out

    def test_shows_open_position(self, pos_db, capsys):
        store = SQLitePositionStore(db_path=pos_db)
        store.save(_pos("BTC/USDT", entry=50000.0))
        rc = cmd_portfolio(pos_db)
        assert rc == 0
        out = capsys.readouterr().out
        assert "BTC/USDT" in out
        assert "50,000" in out

    def test_shows_multiple_positions(self, pos_db, capsys):
        store = SQLitePositionStore(db_path=pos_db)
        store.save(_pos("BTC/USDT"))
        store.save(_pos("ETH/USDT", entry=3000.0))
        cmd_portfolio(pos_db)
        out = capsys.readouterr().out
        assert "BTC/USDT" in out
        assert "ETH/USDT" in out

    def test_shows_stop_loss(self, pos_db, capsys):
        store = SQLitePositionStore(db_path=pos_db)
        store.save(_pos(stop_loss=45000.0))
        cmd_portfolio(pos_db)
        out = capsys.readouterr().out
        assert "45,000" in out

    def test_shows_dash_when_no_sl(self, pos_db, capsys):
        store = SQLitePositionStore(db_path=pos_db)
        store.save(_pos(stop_loss=None))
        cmd_portfolio(pos_db)
        out = capsys.readouterr().out
        assert "—" in out

    def test_total_count_shown(self, pos_db, capsys):
        store = SQLitePositionStore(db_path=pos_db)
        store.save(_pos("BTC/USDT"))
        store.save(_pos("ETH/USDT"))
        cmd_portfolio(pos_db)
        out = capsys.readouterr().out
        assert "2" in out


# ── cmd_trades ────────────────────────────────────────────────────────────────

class TestCmdTrades:
    def test_empty_trades_message(self, journal_db, capsys):
        SQLiteJournalStore(db_path=journal_db)  # empty
        rc = cmd_trades(journal_db)
        assert rc == 0
        assert "no completed trades" in capsys.readouterr().out

    def test_shows_recent_trades(self, journal_db, capsys):
        store = SQLiteJournalStore(db_path=journal_db)
        store.save(_trade("BTC/USDT", pnl=100.0))
        cmd_trades(journal_db)
        out = capsys.readouterr().out
        assert "BTC/USDT" in out

    def test_respects_n_limit(self, journal_db, capsys):
        store = SQLiteJournalStore(db_path=journal_db)
        for i in range(25):
            store.save(_trade(pnl=float(i + 1)))
        cmd_trades(journal_db, n=5)
        out = capsys.readouterr().out
        # Should show "last 5 of 25 total"
        assert "5" in out
        assert "25" in out

    def test_shows_pnl(self, journal_db, capsys):
        store = SQLiteJournalStore(db_path=journal_db)
        store.save(_trade(pnl=123.45))
        cmd_trades(journal_db)
        out = capsys.readouterr().out
        assert "123" in out

    def test_shows_reason(self, journal_db, capsys):
        store = SQLiteJournalStore(db_path=journal_db)
        store.save(_trade(reason="stop_loss"))
        cmd_trades(journal_db)
        assert "stop_loss" in capsys.readouterr().out

    def test_negative_pnl_shown_with_minus(self, journal_db, capsys):
        store = SQLiteJournalStore(db_path=journal_db)
        store.save(_trade(pnl=-75.0))
        cmd_trades(journal_db)
        out = capsys.readouterr().out
        assert "-" in out


# ── cmd_stats ─────────────────────────────────────────────────────────────────

class TestCmdStats:
    def test_empty_journal_shows_zeros(self, journal_db, capsys):
        SQLiteJournalStore(db_path=journal_db)
        rc = cmd_stats(journal_db)
        assert rc == 0
        out = capsys.readouterr().out
        assert "0" in out

    def test_shows_win_rate(self, journal_db, capsys):
        store = SQLiteJournalStore(db_path=journal_db)
        store.save(_trade(pnl=100.0))
        store.save(_trade(pnl=100.0))
        store.save(_trade(pnl=-50.0))
        cmd_stats(journal_db)
        out = capsys.readouterr().out
        assert "66.67" in out  # 2/3 win rate

    def test_shows_profit_factor(self, journal_db, capsys):
        store = SQLiteJournalStore(db_path=journal_db)
        store.save(_trade(pnl=200.0))
        store.save(_trade(pnl=-100.0))
        cmd_stats(journal_db)
        out = capsys.readouterr().out
        assert "2.0" in out

    def test_shows_total_trades(self, journal_db, capsys):
        store = SQLiteJournalStore(db_path=journal_db)
        for _ in range(7):
            store.save(_trade())
        cmd_stats(journal_db)
        assert "7" in capsys.readouterr().out

    def test_infinite_pf_shown_as_symbol(self, journal_db, capsys):
        store = SQLiteJournalStore(db_path=journal_db)
        store.save(_trade(pnl=100.0))  # all wins → PF = ∞
        cmd_stats(journal_db)
        assert "∞" in capsys.readouterr().out


# ── cmd_all ───────────────────────────────────────────────────────────────────

class TestCmdAll:
    def test_all_shows_all_sections(self, pos_db, journal_db, capsys):
        pos_store = SQLitePositionStore(db_path=pos_db)
        pos_store.save(_pos("BTC/USDT"))
        jrn_store = SQLiteJournalStore(db_path=journal_db)
        jrn_store.save(_trade(pnl=50.0))
        rc = cmd_all(pos_db, journal_db)
        assert rc == 0
        out = capsys.readouterr().out
        assert "OPEN POSITIONS" in out
        assert "PERFORMANCE" in out
        assert "RECENT TRADES" in out


# ── main() ────────────────────────────────────────────────────────────────────

class TestStatusCliMain:
    def test_main_no_subcommand_runs_all(self, tmp_path, capsys):
        pos_db = str(tmp_path / "p.db")
        jrn_db = str(tmp_path / "j.db")
        SQLitePositionStore(db_path=pos_db)
        SQLiteJournalStore(db_path=jrn_db)
        rc = main(["--pos-db", pos_db, "--journal-db", jrn_db])
        assert rc == 0
        out = capsys.readouterr().out
        assert "OPEN POSITIONS" in out

    def test_main_portfolio_subcommand(self, tmp_path, capsys):
        pos_db = str(tmp_path / "p.db")
        SQLitePositionStore(db_path=pos_db)
        rc = main(["--pos-db", pos_db, "--journal-db", str(tmp_path / "j.db"), "portfolio"])
        assert rc == 0

    def test_main_trades_subcommand(self, tmp_path, capsys):
        jrn_db = str(tmp_path / "j.db")
        SQLiteJournalStore(db_path=jrn_db)
        rc = main(["--pos-db", str(tmp_path / "p.db"), "--journal-db", jrn_db, "trades"])
        assert rc == 0

    def test_main_stats_subcommand(self, tmp_path, capsys):
        jrn_db = str(tmp_path / "j.db")
        SQLiteJournalStore(db_path=jrn_db)
        rc = main(["--pos-db", str(tmp_path / "p.db"), "--journal-db", jrn_db, "stats"])
        assert rc == 0

    def test_main_trades_with_n_flag(self, tmp_path, capsys):
        jrn_db = str(tmp_path / "j.db")
        store = SQLiteJournalStore(db_path=jrn_db)
        for i in range(10):
            store.save(_trade(pnl=float(i + 1)))
        main(["--journal-db", jrn_db, "--pos-db", str(tmp_path / "p.db"), "trades", "-n", "3"])
        out = capsys.readouterr().out
        assert "3" in out
        assert "10" in out
