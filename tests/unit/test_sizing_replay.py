"""
Tests for src/backtest/sizing_replay.py — counterfactual position-sizing
replay over an already-realised sequence of trades.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.backtest.sizing_replay import (
    ReplayTrade,
    SizingConfig,
    SkipReason,
    replay_with_sizing,
)


def _t(minutes: int) -> datetime:
    return datetime(2026, 8, 27, 0, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def _trade(entry_min: int, exit_min: int, ret: float, symbol: str = "BTC/KRW") -> ReplayTrade:
    return ReplayTrade(
        symbol=symbol,
        entry_time=_t(entry_min),
        exit_time=_t(exit_min),
        return_pct=ret,
    )


def _cfg(pct: float, **kw) -> SizingConfig:
    defaults = dict(
        initial_capital=Decimal("1000000"),
        position_size_pct=pct,
        max_open_positions=4,
        min_order_krw=Decimal("10000"),
        cash_cap_ratio=Decimal("0.99"),
    )
    defaults.update(kw)
    return SizingConfig(**defaults)  # type: ignore[arg-type]


class TestProportionality:
    def test_doubling_size_doubles_pnl_on_single_trade(self) -> None:
        trades = [_trade(0, 60, 0.02)]
        small = replay_with_sizing(trades, _cfg(0.10))
        large = replay_with_sizing(trades, _cfg(0.20))

        pnl_small = small.final_capital - small.config.initial_capital
        pnl_large = large.final_capital - large.config.initial_capital
        assert pnl_large == pnl_small * 2

    def test_single_trade_pnl_matches_hand_calculation(self) -> None:
        # 1,000,000 KRW * 25% = 250,000 sized; +2% -> +5,000 KRW
        result = replay_with_sizing([_trade(0, 60, 0.02)], _cfg(0.25))
        assert result.final_capital == Decimal("1005000")
        assert len(result.executed) == 1
        assert result.executed[0].size == Decimal("250000")

    def test_sequential_trades_compound(self) -> None:
        # two non-overlapping +10% trades at 100% sizing -> 1.1 * 1.1
        trades = [_trade(0, 10, 0.10), _trade(20, 30, 0.10)]
        result = replay_with_sizing(trades, _cfg(1.0, cash_cap_ratio=Decimal("1.0")))
        assert result.final_capital == Decimal("1210000")


class TestSkipRules:
    def test_slot_limit_skips_overlapping_trade(self) -> None:
        trades = [
            _trade(0, 100, 0.01, "BTC/KRW"),
            _trade(10, 110, 0.01, "ETH/KRW"),
        ]
        result = replay_with_sizing(trades, _cfg(0.25, max_open_positions=1))

        assert len(result.executed) == 1
        assert len(result.skipped) == 1
        assert result.skipped[0].reason is SkipReason.SLOT_LIMIT
        assert result.skipped[0].symbol == "ETH/KRW"

    def test_no_skip_when_trades_do_not_overlap(self) -> None:
        trades = [
            _trade(0, 10, 0.01, "BTC/KRW"),
            _trade(20, 30, 0.01, "ETH/KRW"),
        ]
        result = replay_with_sizing(trades, _cfg(0.25, max_open_positions=1))
        assert len(result.executed) == 2
        assert result.skipped == ()

    def test_insufficient_cash_skips_trade(self) -> None:
        # capital 9,000: even 99% of it (8,910) is below the 10,000 minimum
        # order -> the order cannot be placed at all.
        result = replay_with_sizing(
            [_trade(0, 60, 0.02)],
            _cfg(0.25, initial_capital=Decimal("9000")),
        )
        assert result.executed == ()
        assert len(result.skipped) == 1
        assert result.skipped[0].reason is SkipReason.INSUFFICIENT_CASH
        assert result.final_capital == Decimal("9000")

    def test_size_below_minimum_is_bumped_up_when_cash_allows(self) -> None:
        # Mirrors engine.py's behaviour: 20,000 * 25% = 5,000 is below the
        # 10,000 KRW exchange minimum, so the order is bumped up, not skipped.
        result = replay_with_sizing(
            [_trade(0, 60, 0.10)],
            _cfg(0.25, initial_capital=Decimal("20000")),
        )
        assert len(result.executed) == 1
        assert result.executed[0].size == Decimal("10000")
        assert result.final_capital == Decimal("21000")

    def test_cash_cap_limits_size_of_second_concurrent_entry(self) -> None:
        # 100% sizing, two overlapping trades: the first consumes 99% of cash,
        # the second can only use 99% of what remains (1% of capital).
        trades = [
            _trade(0, 100, 0.0, "BTC/KRW"),
            _trade(10, 110, 0.0, "ETH/KRW"),
        ]
        result = replay_with_sizing(trades, _cfg(1.0, initial_capital=Decimal("10000000")))
        assert len(result.executed) == 2
        assert result.executed[0].size == Decimal("9900000")
        assert result.executed[1].size == Decimal("99000")

    def test_position_closes_before_later_entry_frees_the_slot(self) -> None:
        trades = [
            _trade(0, 30, 0.10, "BTC/KRW"),
            _trade(30, 60, 0.10, "ETH/KRW"),
        ]
        result = replay_with_sizing(trades, _cfg(1.0, max_open_positions=1,
                                                 cash_cap_ratio=Decimal("1.0")))
        assert len(result.executed) == 2
        assert result.final_capital == Decimal("1210000")


class TestEmptyAndEdges:
    def test_empty_trade_list_returns_initial_capital(self) -> None:
        result = replay_with_sizing([], _cfg(0.25))
        assert result.final_capital == Decimal("1000000")
        assert result.executed == ()
        assert result.skipped == ()
        assert result.equity_points == ()

    def test_zero_sizing_rejected(self) -> None:
        with pytest.raises(ValueError):
            _cfg(0.0)

    def test_sizing_above_one_rejected(self) -> None:
        with pytest.raises(ValueError):
            _cfg(1.5)

    def test_non_positive_capital_rejected(self) -> None:
        with pytest.raises(ValueError):
            _cfg(0.25, initial_capital=Decimal("0"))

    def test_non_positive_slot_limit_rejected(self) -> None:
        with pytest.raises(ValueError):
            _cfg(0.25, max_open_positions=0)

    def test_negative_min_order_rejected(self) -> None:
        with pytest.raises(ValueError):
            _cfg(0.25, min_order_krw=Decimal("-1"))

    def test_out_of_range_cash_cap_rejected(self) -> None:
        with pytest.raises(ValueError):
            _cfg(0.25, cash_cap_ratio=Decimal("1.5"))

    def test_total_return_pct(self) -> None:
        result = replay_with_sizing([_trade(0, 60, 0.02)], _cfg(0.25))
        assert result.total_return_pct == pytest.approx(0.5)

    def test_trades_are_processed_in_entry_time_order(self) -> None:
        trades = [_trade(20, 30, 0.10, "ETH/KRW"), _trade(0, 10, 0.10, "BTC/KRW")]
        result = replay_with_sizing(trades, _cfg(1.0, cash_cap_ratio=Decimal("1.0")))
        assert [e.symbol for e in result.executed] == ["BTC/KRW", "ETH/KRW"]


class TestEquityPoints:
    def test_equity_points_are_time_ordered_and_end_at_final_capital(self) -> None:
        trades = [_trade(0, 30, 0.05), _trade(40, 70, -0.02)]
        result = replay_with_sizing(trades, _cfg(0.25))

        times = [p.timestamp for p in result.equity_points]
        assert times == sorted(times)
        assert result.equity_points[-1].equity == result.final_capital

    def test_batch_settlement_does_not_dip_the_equity_path(self) -> None:
        # Three winners all close well before the next entry, so they are
        # settled in one batch. Equity must never dip below its previous
        # value — a naive batch settlement drops the cost basis of the
        # not-yet-settled positions and fakes a large drawdown.
        trades = [
            _trade(0, 10, 0.05, "BTC/KRW"),
            _trade(1, 11, 0.05, "ETH/KRW"),
            _trade(2, 12, 0.05, "SOL/KRW"),
            _trade(500, 510, 0.01, "XRP/KRW"),
        ]
        result = replay_with_sizing(trades, _cfg(0.25))
        equities = [p.equity for p in result.equity_points]
        assert equities == sorted(equities), equities

    def test_executed_trade_records_equity_at_entry_and_pnl(self) -> None:
        result = replay_with_sizing([_trade(0, 30, 0.05)], _cfg(0.20))
        ex = result.executed[0]
        assert ex.equity_at_entry == Decimal("1000000")
        assert ex.size == Decimal("200000")
        assert ex.pnl == Decimal("10000")


class TestFromTradeRecords:
    def test_converts_journal_style_rows_to_replay_trades(self) -> None:
        from src.backtest.sizing_replay import to_replay_trades

        rows = [
            {
                "symbol": "BTC/KRW",
                "amount": "0.01",
                "entry_price": "100000000",
                "entry_time": _t(0).timestamp(),
                "exit_time": _t(60).timestamp(),
                "pnl": "20000",
            }
        ]
        trades = to_replay_trades(rows)
        assert len(trades) == 1
        # notional = 1,000,000 ; pnl 20,000 -> +2%
        assert trades[0].return_pct == pytest.approx(0.02)
        assert trades[0].symbol == "BTC/KRW"
        assert trades[0].entry_time == _t(0)

    def test_zero_notional_row_is_rejected(self) -> None:
        from src.backtest.sizing_replay import to_replay_trades

        rows = [
            {
                "symbol": "BTC/KRW",
                "amount": "0",
                "entry_price": "100000000",
                "entry_time": _t(0).timestamp(),
                "exit_time": _t(60).timestamp(),
                "pnl": "0",
            }
        ]
        with pytest.raises(ValueError):
            to_replay_trades(rows)
