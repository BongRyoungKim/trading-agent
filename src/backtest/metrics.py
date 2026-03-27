"""
Performance metrics calculation for backtest results.
All functions are pure: receive data, return computed values.
"""
from __future__ import annotations

import math
from decimal import Decimal

import numpy as np
import pandas as pd

from src.backtest.models import Trade


def total_return_pct(initial: Decimal, final: Decimal) -> float:
    """Total return percentage over the backtest period."""
    if initial == 0:
        return 0.0
    return float((final - initial) / initial) * 100


def annualized_return_pct(
    total_return: float, start_date, end_date, trading_days_per_year: int = 365
) -> float:
    """Annualized return assuming compounding."""
    days = (end_date - start_date).days
    if days <= 0:
        return 0.0
    years = days / trading_days_per_year
    factor = 1 + total_return / 100
    if factor <= 0:
        return -100.0
    return (factor ** (1 / years) - 1) * 100


def max_drawdown_pct(equity_curve: pd.Series) -> float:
    """
    Maximum peak-to-trough drawdown as a positive percentage.
    e.g. 15.3 means -15.3% from peak.
    """
    if equity_curve.empty:
        return 0.0
    rolling_max = equity_curve.cummax()
    drawdown = (equity_curve - rolling_max) / rolling_max
    return abs(float(drawdown.min())) * 100


def sharpe_ratio(
    returns: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """
    Annualized Sharpe Ratio.
    `returns` should be a series of period returns (e.g. daily % returns as decimals).
    """
    excess = returns - risk_free_rate / periods_per_year
    std = excess.std(ddof=1)
    if std == 0 or math.isnan(std):
        return 0.0
    return float(excess.mean() / std * math.sqrt(periods_per_year))


def sortino_ratio(
    returns: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """
    Annualized Sortino Ratio (uses downside deviation instead of total std).
    """
    excess = returns - risk_free_rate / periods_per_year
    downside = excess[excess < 0]
    if downside.empty:
        return float("inf") if excess.mean() > 0 else 0.0
    downside_std = math.sqrt((downside**2).mean())
    if downside_std == 0:
        return 0.0
    return float(excess.mean() / downside_std * math.sqrt(periods_per_year))


def win_rate_pct(trades: list[Trade]) -> float:
    """Percentage of trades that are winners."""
    if not trades:
        return 0.0
    winners = sum(1 for t in trades if t.is_winner)
    return winners / len(trades) * 100


def profit_factor(trades: list[Trade]) -> float:
    """
    Gross profit / gross loss.
    Returns inf if there are no losing trades.
    """
    gross_profit = sum(float(t.net_pnl) for t in trades if t.net_pnl > 0)
    gross_loss = abs(sum(float(t.net_pnl) for t in trades if t.net_pnl < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def build_equity_curve(
    initial_capital: Decimal, trades: list[Trade]
) -> pd.Series:
    """
    Build a time-indexed equity curve from a list of trades.
    Returns a Series indexed by trade exit_time.
    """
    if not trades:
        return pd.Series(dtype=float)

    sorted_trades = sorted(trades, key=lambda t: t.exit_time)
    capital = float(initial_capital)
    timestamps = []
    values = []

    for trade in sorted_trades:
        capital += float(trade.net_pnl)
        timestamps.append(trade.exit_time)
        values.append(capital)

    return pd.Series(values, index=pd.DatetimeIndex(timestamps))


def compute_all(
    initial_capital: Decimal,
    final_capital: Decimal,
    trades: list[Trade],
    start_date,
    end_date,
) -> dict:
    """
    Compute all performance metrics at once.
    Returns a dict of metric_name → value.
    """
    equity = build_equity_curve(initial_capital, trades)
    trade_returns = pd.Series([t.return_pct / 100 for t in trades]) if trades else pd.Series(dtype=float)

    t_return = total_return_pct(initial_capital, final_capital)

    return {
        "total_return_pct": t_return,
        "annualized_return_pct": annualized_return_pct(t_return, start_date, end_date),
        "max_drawdown_pct": max_drawdown_pct(equity) if not equity.empty else 0.0,
        "sharpe_ratio": sharpe_ratio(trade_returns) if len(trade_returns) > 1 else 0.0,
        "sortino_ratio": sortino_ratio(trade_returns) if len(trade_returns) > 1 else 0.0,
        "win_rate_pct": win_rate_pct(trades),
        "profit_factor": profit_factor(trades),
        "total_trades": len(trades),
    }
