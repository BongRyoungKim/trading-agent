"""
Bar-level backtest engine that shares its exit-rule math with the live
TradingEngine via src/risk/exit_rules.py — SL/TP clamping, trailing-stop
ratchet, time-stop, and the signal-exit min-hold/min-profit gate are called
from that one module, not reimplemented here. This is the structural
guarantee that a backtest result can never silently drift from what the
live engine would actually do (see docs/research-protocol.md).

Explicitly documented simplifications (not present in the live engine):
  - Single position at a time, one symbol per call (no shared-capital /
    max_open_positions contention across a multi-symbol universe).
  - No commission-based cash/position-limit checks from RiskManager.
    check_can_open_position(); this engine assumes capital is always
    available to open the next signalled entry.
  - Intrabar path for SL/TP/trailing is approximated (see `_bar_path`)
    because only OHLC is available, not tick data.
  - Entry/exit prices use the bar's own OHLC (no slippage model).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from src.backtest.models import Trade
from src.risk.exit_rules import (
    clamp_stop_loss_and_size_take_profit,
    evaluate_open_position_exit,
    is_signal_exit_allowed,
)
from src.risk.manager import RiskManager
from src.strategy.base import BaseStrategy
from src.strategy.models import SignalAction


@dataclass(frozen=True)
class ExitBacktestConfig:
    """
    Risk knobs — mirrors the live-tunable properties on TradingEngine
    (sl_ceiling_pct, sl_floor_pct, atr_multiplier, tp_rr_multiplier,
    trailing_stop_pct) plus the engine's currently-hardcoded time-stop /
    signal-exit constants, now exposed as parameters so research can vary
    them without touching engine.py.
    """

    sl_ceiling_pct: Decimal
    sl_floor_pct: Decimal
    atr_multiplier: float
    tp_rr_multiplier: Decimal
    trailing_stop_pct: Decimal | None = None
    time_stop_minutes: float = 60.0
    time_stop_loss_pct: Decimal = Decimal("0.5")
    min_signal_hold_seconds: float = 1800.0
    min_signal_profit_pct: float = 0.3
    commission_rate: Decimal = Decimal("0.001")  # matches engine.py _PAPER_COMMISSION_RATE
    bar_minutes: int = 15
    window: int = 200  # matches engine.py get_ohlcv_dataframe(..., limit=200)


@dataclass
class _OpenPosition:
    entry_bar: int
    entry_time: pd.Timestamp
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal


def _bar_path(row: pd.Series) -> list[Decimal]:
    """
    Approximate the intrabar price path from OHLC only (no tick data):
    bullish candle (close >= open) -> open, low, high, close;
    bearish candle (close < open)  -> open, high, low, close.
    Documented simplification, see module docstring.
    """
    o, h, low_, c = (Decimal(str(row.open)), Decimal(str(row.high)),
                      Decimal(str(row.low)), Decimal(str(row.close)))
    if c >= o:
        return [o, low_, h, c]
    return [o, h, low_, c]


def run_exit_backtest(
    df: pd.DataFrame,
    strategy: BaseStrategy,
    risk_manager: RiskManager,
    config: ExitBacktestConfig,
    symbol: str = "",
) -> list[Trade]:
    """
    Run a single-symbol, single-position-at-a-time backtest.

    Args:
        df: OHLCV DataFrame, oldest row first, columns open/high/low/close/
            volume/timestamp (matches BaseStrategy.generate_signal's contract).
        strategy: any BaseStrategy — signals decide entries/signal-exits.
        risk_manager: used only for its calculate_stop_loss() (ATR/fixed-pct
            raw SL distance) — matches TradingEngine._open_position exactly.
        config: risk knobs, see ExitBacktestConfig.
        symbol: label attached to each returned Trade.

    Returns:
        list[Trade] (src.backtest.models.Trade) — feed into
        src.backtest.metrics.compute_all() for performance stats.
    """
    trades: list[Trade] = []
    pos: _OpenPosition | None = None
    window = config.window
    n = len(df)

    for j in range(window - 1, n):
        row = df.iloc[j]
        win_df = df.iloc[j - window + 1 : j + 1].reset_index(drop=True)

        if pos is not None:
            closed = _try_close_open_position(trades, pos, row, strategy, win_df, config, symbol)
            if closed:
                pos = None
            continue

        signal = strategy.generate_signal(win_df)
        if signal.action == SignalAction.BUY:
            pos = _open_position(j, row, signal, risk_manager, config)

    return trades


def _open_position(
    bar_idx: int,
    row: pd.Series,
    signal,
    risk_manager: RiskManager,
    config: ExitBacktestConfig,
) -> _OpenPosition:
    price = Decimal(str(signal.metadata.get("price", row.close)))
    atr_val = signal.metadata.get("atr") if signal.metadata else None
    raw_stop_loss = risk_manager.calculate_stop_loss(
        price, side="buy",
        atr_value=float(atr_val) if atr_val else None,
        atr_multiplier=config.atr_multiplier,
    )
    stop_loss, take_profit = clamp_stop_loss_and_size_take_profit(
        entry_price=price,
        raw_stop_loss=raw_stop_loss,
        sl_ceiling_pct=config.sl_ceiling_pct,
        sl_floor_pct=config.sl_floor_pct,
        tp_rr_multiplier=config.tp_rr_multiplier,
    )
    return _OpenPosition(
        entry_bar=bar_idx, entry_time=row.timestamp, entry_price=price,
        stop_loss=stop_loss, take_profit=take_profit,
    )


def _make_trade(
    pos: _OpenPosition, exit_time, exit_price: Decimal, reason: str,
    config: ExitBacktestConfig, symbol: str,
) -> Trade:
    amount = Decimal("1")
    commission = (pos.entry_price + exit_price) * amount * config.commission_rate
    return Trade(
        symbol=symbol, side="buy", entry_price=pos.entry_price, exit_price=exit_price,
        amount=amount, entry_time=pos.entry_time, exit_time=exit_time,
        commission=commission, exit_reason=reason,
    )


def _try_close_open_position(
    trades: list[Trade],
    pos: _OpenPosition,
    row: pd.Series,
    strategy: BaseStrategy,
    win_df: pd.DataFrame,
    config: ExitBacktestConfig,
    symbol: str,
) -> bool:
    """Returns True if the position was closed this bar (and appends the Trade)."""
    for point in _bar_path(row):
        hold_minutes = (row.timestamp - pos.entry_time).total_seconds() / 60
        decision = evaluate_open_position_exit(
            entry_price=pos.entry_price,
            hold_minutes=hold_minutes,
            current_price=point,
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            trailing_stop_pct=config.trailing_stop_pct,
            time_stop_minutes=config.time_stop_minutes,
            time_stop_loss_pct=config.time_stop_loss_pct,
        )
        pos.stop_loss = decision.updated_stop_loss
        if decision.should_exit:
            trades.append(_make_trade(pos, row.timestamp, point, decision.reason, config, symbol))
            return True

    signal = strategy.generate_signal(win_df)
    if signal.action == SignalAction.SELL:
        hold_seconds = (row.timestamp - pos.entry_time).total_seconds()
        price_now = Decimal(str(signal.metadata.get("price", row.close)))
        unrealized_pct = float((price_now - pos.entry_price) / pos.entry_price * 100)
        if is_signal_exit_allowed(hold_seconds, unrealized_pct,
                                   config.min_signal_hold_seconds, config.min_signal_profit_pct):
            trades.append(_make_trade(pos, row.timestamp, price_now, "signal", config, symbol))
            return True

    return False
