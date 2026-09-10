"""
Unit tests for src/backtest/exit_backtest_engine.py.

Uses a tiny scripted fake strategy (not a real indicator-based strategy) so
each test pins down one exit path (SL / TP / time-stop / signal-exit) with
hand-computed numbers, independent of any real strategy's signal logic.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.backtest.exit_backtest_engine import ExitBacktestConfig, run_exit_backtest
from src.risk.manager import PortfolioState, RiskManager
from src.strategy.base import BaseStrategy
from src.strategy.models import Signal, SignalAction

WINDOW = 3


class _ScriptedStrategy(BaseStrategy):
    """Returns `actions[call_index]` each time generate_signal is called
    (clamped to the last action once exhausted). `atr` is constant."""

    def __init__(self, actions: list[SignalAction], atr: float = 1.0) -> None:
        self._actions = actions
        self._call = 0
        self._atr = atr

    @property
    def name(self) -> str:
        return "scripted"

    def get_parameters(self) -> dict:
        return {}

    def min_required_bars(self) -> int:
        return 1

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        row = data.iloc[-1]
        idx = min(self._call, len(self._actions) - 1)
        action = self._actions[idx]
        self._call += 1
        return Signal(
            symbol="TEST", action=action, strength=1.0, reason="scripted",
            timestamp=row["timestamp"],
            metadata={"price": float(row["close"]), "atr": self._atr},
        )


def _row(ts, o, h, l, c) -> dict:
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": 100.0}


def _base_time(i: int) -> datetime:
    return datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=15 * i)


def _make_risk_manager() -> RiskManager:
    settings = MagicMock()
    settings.max_position_risk = 0.02
    state = PortfolioState(capital=Decimal("10000000"), peak_capital=Decimal("10000000"))
    return RiskManager(settings, state)


def _base_config(**overrides) -> ExitBacktestConfig:
    kwargs = dict(
        sl_ceiling_pct=Decimal("1.5"), sl_floor_pct=Decimal("3.0"),
        atr_multiplier=2.0, tp_rr_multiplier=Decimal("1.5"),
        trailing_stop_pct=None, window=WINDOW,
    )
    kwargs.update(overrides)
    return ExitBacktestConfig(**kwargs)


class TestStopLossExit:
    def test_stop_loss_triggers_on_crash(self) -> None:
        # 3 flat warm-up bars at 100 (entry at bar index WINDOW-1=2, price 100).
        # ATR=1.0, atr_multiplier=2.0 -> raw SL=98; ceiling 1.5%->98.5,
        # floor 3.0%->97 -> clamped SL stays 98 (within bounds). TP=100+2*1.5=103.
        rows = [_row(_base_time(i), 100, 100, 100, 100) for i in range(WINDOW)]
        # Next bar crashes hard: bearish candle -> path = open, high, low, close.
        rows.append(_row(_base_time(WINDOW), 95, 95, 88, 90))
        df = pd.DataFrame(rows)

        strategy = _ScriptedStrategy([SignalAction.BUY] + [SignalAction.HOLD] * 10)
        trades = run_exit_backtest(df, strategy, _make_risk_manager(), _base_config())

        assert len(trades) == 1
        assert trades[0].exit_reason == "stop_loss"
        assert trades[0].entry_price == Decimal("100")
        assert trades[0].exit_price == Decimal("95")  # open of the crash bar, first path point <= SL(98)


class TestTakeProfitExit:
    def test_take_profit_triggers_on_rally(self) -> None:
        rows = [_row(_base_time(i), 100, 100, 100, 100) for i in range(WINDOW)]
        # Bullish candle -> path = open, low, high, close. open(105) already >= TP(103).
        rows.append(_row(_base_time(WINDOW), 105, 106, 104, 110))
        df = pd.DataFrame(rows)

        strategy = _ScriptedStrategy([SignalAction.BUY] + [SignalAction.HOLD] * 10)
        trades = run_exit_backtest(df, strategy, _make_risk_manager(), _base_config())

        assert len(trades) == 1
        assert trades[0].exit_reason == "take_profit"
        assert trades[0].exit_price == Decimal("105")


class TestTimeStopExit:
    def test_time_stop_triggers_after_60_minutes_when_losing(self) -> None:
        # entry at 100, SL=98/TP=103 (as above). Keep price at 99.6 (inside
        # SL/TP band, not losing enough) until hold_minutes >= 60 (4 bars),
        # then drop to 99.0 (< entry*0.995=99.5) to trigger the time-stop.
        rows = [_row(_base_time(i), 100, 100, 100, 100) for i in range(WINDOW)]
        for i in range(WINDOW, WINDOW + 3):  # bars at +15,+30,+45 min: 99.6, flat candle
            rows.append(_row(_base_time(i), 99.6, 99.6, 99.6, 99.6))
        # +60 min bar: flat candle at 99.0 (< 99.5 threshold) -> time-stop fires
        rows.append(_row(_base_time(WINDOW + 3), 99.0, 99.0, 99.0, 99.0))
        df = pd.DataFrame(rows)

        strategy = _ScriptedStrategy([SignalAction.BUY] + [SignalAction.HOLD] * 10)
        trades = run_exit_backtest(df, strategy, _make_risk_manager(), _base_config())

        assert len(trades) == 1
        assert trades[0].exit_reason == "time_stop"


class TestSignalExit:
    def test_signal_sell_honoured_once_min_hold_and_profit_met(self) -> None:
        rows = [_row(_base_time(i), 100, 100, 100, 100) for i in range(WINDOW)]
        # Hold for well over 30 min (min_signal_hold_seconds=1800) with a
        # profitable price (>= +0.3%) before the scripted SELL fires.
        for i in range(WINDOW, WINDOW + 4):
            rows.append(_row(_base_time(i), 100.5, 100.5, 100.5, 100.5))
        df = pd.DataFrame(rows)

        # BUY on first call; HOLD while position accrues hold-time; SELL after.
        actions = [SignalAction.BUY] + [SignalAction.HOLD] * 3 + [SignalAction.SELL] * 5
        strategy = _ScriptedStrategy(actions)
        trades = run_exit_backtest(df, strategy, _make_risk_manager(), _base_config())

        assert len(trades) == 1
        assert trades[0].exit_reason == "signal"
        assert trades[0].exit_price == Decimal("100.5")

    def test_signal_sell_suppressed_before_min_hold(self) -> None:
        # SELL fires immediately (call index 1, hold ~0s) — must NOT close;
        # position instead survives until the flat data runs out.
        rows = [_row(_base_time(i), 100, 100, 100, 100) for i in range(WINDOW)]
        rows.append(_row(_base_time(WINDOW), 100, 100, 100, 100))
        df = pd.DataFrame(rows)

        actions = [SignalAction.BUY, SignalAction.SELL]
        strategy = _ScriptedStrategy(actions)
        trades = run_exit_backtest(df, strategy, _make_risk_manager(), _base_config())

        assert len(trades) == 0


class TestTrailingStop:
    def test_trailing_stop_ratchets_and_can_close_a_winner(self) -> None:
        rows = [_row(_base_time(i), 100, 100, 100, 100) for i in range(WINDOW)]
        # Rally to 110 (bullish candle, doesn't touch original TP=103... wait
        # TP would already trigger; use a very high TP via low tp_rr so we
        # isolate trailing-stop behavior instead. Use a config with a huge
        # tp_rr_multiplier so TP is unreachable, isolating the trailing exit.
        rows.append(_row(_base_time(WINDOW), 105, 110, 105, 110))
        # Pull back sharply the next bar -> trailing SL (from price 110,
        # trailing 2% -> 107.8) should catch this.
        rows.append(_row(_base_time(WINDOW + 1), 106, 106, 100, 100))
        df = pd.DataFrame(rows)

        strategy = _ScriptedStrategy([SignalAction.BUY] + [SignalAction.HOLD] * 10)
        config = _base_config(trailing_stop_pct=Decimal("2.0"), tp_rr_multiplier=Decimal("100"))
        trades = run_exit_backtest(df, strategy, _make_risk_manager(), config)

        assert len(trades) == 1
        assert trades[0].exit_reason == "stop_loss"
        assert trades[0].exit_price == Decimal("106")  # open of pullback bar, first path point below 107.8


class TestSymbolLabel:
    def test_symbol_label_propagated_to_trades(self) -> None:
        rows = [_row(_base_time(i), 100, 100, 100, 100) for i in range(WINDOW)]
        rows.append(_row(_base_time(WINDOW), 95, 95, 88, 90))
        df = pd.DataFrame(rows)
        strategy = _ScriptedStrategy([SignalAction.BUY] + [SignalAction.HOLD] * 10)
        trades = run_exit_backtest(df, strategy, _make_risk_manager(), _base_config(), symbol="BTC/KRW")
        assert trades[0].symbol == "BTC/KRW"
