"""
Position sizing models.
All functions are pure: receive parameters, return position size as Decimal.
"""
from __future__ import annotations

from decimal import Decimal


def fixed_fraction(
    capital: Decimal,
    risk_fraction: float,
    entry_price: Decimal,
    stop_loss_price: Decimal,
) -> Decimal:
    """
    Fixed fractional position sizing (most common method).

    Risks a fixed percentage of capital on each trade.
    Position size is derived from the distance between entry and stop-loss.

    Args:
        capital:         Total available capital.
        risk_fraction:   Fraction of capital to risk (e.g. 0.02 = 2%).
        entry_price:     Planned entry price.
        stop_loss_price: Stop-loss price.

    Returns:
        Position size in base currency units.

    Example:
        Capital = $10,000, risk = 2%, entry = $50,000, stop = $48,000
        Risk amount = $200
        Risk per unit = $50,000 - $48,000 = $2,000
        Size = $200 / $2,000 = 0.1 BTC
    """
    risk_amount = capital * Decimal(str(risk_fraction))
    price_risk = abs(entry_price - stop_loss_price)
    if price_risk == 0:
        return Decimal("0")
    return risk_amount / price_risk


def kelly_criterion(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    max_fraction: float = 0.25,
) -> float:
    """
    Kelly Criterion for optimal position sizing.

    f* = (p * b - q) / b
    where p = win probability, q = loss probability, b = win/loss ratio.

    Args:
        win_rate:     Historical win rate (0.0–1.0).
        avg_win:      Average winning trade return (positive float, e.g. 0.05 = 5%).
        avg_loss:     Average losing trade return (positive float, magnitude only).
        max_fraction: Cap to avoid over-leverage (default 25%).

    Returns:
        Fraction of capital to allocate (0.0–max_fraction).
    """
    if avg_loss == 0 or win_rate <= 0:
        return 0.0
    loss_rate = 1 - win_rate
    b = avg_win / avg_loss  # win/loss ratio
    kelly = (win_rate * b - loss_rate) / b
    return max(0.0, min(kelly, max_fraction))


def percent_of_equity(
    capital: Decimal,
    pct: float,
    entry_price: Decimal,
) -> Decimal:
    """
    Simple percent-of-equity sizing (ignores stop-loss).

    Args:
        capital:     Total capital.
        pct:         Fraction of capital to allocate (e.g. 0.10 = 10%).
        entry_price: Entry price per unit.

    Returns:
        Position size in base currency units.
    """
    if entry_price == 0:
        return Decimal("0")
    notional = capital * Decimal(str(pct))
    return notional / entry_price
