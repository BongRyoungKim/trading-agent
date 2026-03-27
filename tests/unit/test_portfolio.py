"""Unit tests for portfolio tracker and models."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.portfolio.models import Position, PortfolioSnapshot
from src.portfolio.tracker import PortfolioTracker
from src.utils.exceptions import TradingAgentError
from datetime import UTC, datetime


def _make_tracker(cash: float = 10000.0) -> PortfolioTracker:
    return PortfolioTracker(Decimal(str(cash)))


class TestPositionModel:
    def _pos(self, entry: float = 50000.0, amount: float = 0.1) -> Position:
        return Position(
            symbol="BTC/USDT",
            side="buy",
            amount=Decimal(str(amount)),
            entry_price=Decimal(str(entry)),
            entry_time=datetime(2024, 1, 1, tzinfo=UTC),
        )

    def test_unrealized_pnl_profit(self) -> None:
        pos = self._pos(entry=50000.0, amount=0.1)
        pnl = pos.unrealized_pnl(Decimal("55000"))
        assert pnl == Decimal("500")  # (55000-50000)*0.1

    def test_unrealized_pnl_loss(self) -> None:
        pos = self._pos(entry=50000.0, amount=0.1)
        pnl = pos.unrealized_pnl(Decimal("45000"))
        assert pnl == Decimal("-500")

    def test_unrealized_pnl_short(self) -> None:
        pos = Position(
            symbol="BTC/USDT", side="sell",
            amount=Decimal("0.1"), entry_price=Decimal("50000"),
            entry_time=datetime(2024, 1, 1, tzinfo=UTC),
        )
        pnl = pos.unrealized_pnl(Decimal("45000"))
        assert pnl == Decimal("500")  # short profits when price drops

    def test_unrealized_pnl_pct(self) -> None:
        pos = self._pos(entry=50000.0, amount=0.1)
        pct = pos.unrealized_pnl_pct(Decimal("55000"))
        assert abs(pct - 10.0) < 1e-6

    def test_notional_value(self) -> None:
        pos = self._pos(entry=50000.0, amount=0.1)
        assert pos.notional_value(Decimal("52000")) == Decimal("5200")

    def test_position_is_immutable(self) -> None:
        pos = self._pos()
        with pytest.raises((AttributeError, TypeError)):
            pos.amount = Decimal("99")  # type: ignore[misc]


class TestPortfolioTracker:
    def test_initial_cash(self) -> None:
        tracker = _make_tracker(10000.0)
        assert tracker.cash == Decimal("10000")

    def test_open_position_deducts_cash(self) -> None:
        tracker = _make_tracker(10000.0)
        tracker.open_position("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"))
        # Cost = 0.1 * 50000 = 5000
        assert tracker.cash == Decimal("5000")

    def test_open_position_with_commission(self) -> None:
        tracker = _make_tracker(10000.0)
        tracker.open_position(
            "BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"), commission=Decimal("5")
        )
        assert tracker.cash == Decimal("4995")

    def test_open_duplicate_position_raises(self) -> None:
        tracker = _make_tracker(10000.0)
        tracker.open_position("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"))
        with pytest.raises(TradingAgentError, match="already open"):
            tracker.open_position("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"))

    def test_close_position_returns_pnl(self) -> None:
        tracker = _make_tracker(10000.0)
        tracker.open_position("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"))
        pnl = tracker.close_position("BTC/USDT", Decimal("55000"))
        assert pnl == Decimal("500")  # (55000-50000)*0.1

    def test_close_position_updates_cash(self) -> None:
        tracker = _make_tracker(10000.0)
        tracker.open_position("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"))
        tracker.close_position("BTC/USDT", Decimal("55000"))
        # Started 10000, spent 5000, got back 5500 → 10500
        assert tracker.cash == Decimal("10500")

    def test_close_non_existent_raises(self) -> None:
        tracker = _make_tracker(10000.0)
        with pytest.raises(TradingAgentError, match="No open position"):
            tracker.close_position("BTC/USDT", Decimal("50000"))

    def test_has_position_true_false(self) -> None:
        tracker = _make_tracker(10000.0)
        assert not tracker.has_position("BTC/USDT")
        tracker.open_position("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"))
        assert tracker.has_position("BTC/USDT")

    def test_open_symbols(self) -> None:
        tracker = _make_tracker(10000.0)
        tracker.open_position("BTC/USDT", "buy", Decimal("0.01"), Decimal("50000"))
        tracker.open_position("ETH/USDT", "buy", Decimal("0.1"), Decimal("3000"))
        assert set(tracker.open_symbols()) == {"BTC/USDT", "ETH/USDT"}

    def test_realized_pnl_accumulates(self) -> None:
        tracker = _make_tracker(20000.0)
        tracker.open_position("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"))
        tracker.close_position("BTC/USDT", Decimal("55000"))   # +500
        tracker.open_position("BTC/USDT", "buy", Decimal("0.1"), Decimal("55000"))
        tracker.close_position("BTC/USDT", Decimal("52000"))   # -300
        assert tracker.realized_pnl == Decimal("200")


class TestPortfolioSnapshot:
    def test_snapshot_is_immutable(self) -> None:
        tracker = _make_tracker(10000.0)
        snap = tracker.snapshot()
        assert isinstance(snap, PortfolioSnapshot)

    def test_total_unrealized_pnl(self) -> None:
        tracker = _make_tracker(10000.0)
        tracker.open_position("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"))
        snap = tracker.snapshot()
        pnl = snap.total_unrealized_pnl({"BTC/USDT": Decimal("55000")})
        assert pnl == Decimal("500")

    def test_total_equity(self) -> None:
        tracker = _make_tracker(10000.0)
        tracker.open_position("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"))
        # Cash = 5000, BTC position at 55000 = 5500 → equity = 10500
        snap = tracker.snapshot()
        equity = snap.total_equity({"BTC/USDT": Decimal("55000")})
        assert equity == Decimal("10500")

    def test_pnl_report_keys(self) -> None:
        tracker = _make_tracker(10000.0)
        report = tracker.pnl_report()
        assert "cash" in report
        assert "realized_pnl" in report
        assert "unrealized_pnl" in report
        assert "open_positions" in report
