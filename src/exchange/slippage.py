"""
Slippage model for paper trading.

Realistic fill prices account for:
  1. Bid-ask spread  — you pay the ask when buying, receive the bid when selling.
  2. Market impact   — large orders move the price against you.

Usage:
    cfg = SlippageConfig(spread_bps=5.0, impact_bps_per_pct=2.0)
    fill_price = apply_slippage(
        price=Decimal("50000"),
        side="buy",
        amount=Decimal("0.1"),
        daily_volume=Decimal("5000"),   # in base-currency units
        config=cfg,
    )
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SlippageConfig:
    """
    Parameters for the slippage model.

    Attributes:
        spread_bps:          One-way cost of bid-ask spread in basis points.
                             A value of 5 means the mid-to-ask (or mid-to-bid)
                             difference is 0.05 % of the mid price.
        impact_bps_per_pct:  Additional slippage in basis points per 1 % of
                             daily volume consumed by the order.
                             Set to 0 to disable market-impact modelling.
    """

    spread_bps: float = 5.0
    impact_bps_per_pct: float = 2.0

    def __post_init__(self) -> None:
        if self.spread_bps < 0:
            raise ValueError("spread_bps must be >= 0")
        if self.impact_bps_per_pct < 0:
            raise ValueError("impact_bps_per_pct must be >= 0")


def apply_slippage(
    price: Decimal,
    side: str,
    amount: Decimal,
    daily_volume: Decimal,
    config: SlippageConfig,
) -> Decimal:
    """
    Return a realistic fill price for a market order.

    Args:
        price:        Mid-market last price.
        side:         "buy" or "sell".
        amount:       Order size in base-currency units.
        daily_volume: Recent daily volume in base-currency units.
                      Pass Decimal("0") to skip market-impact calculation.
        config:       Slippage parameters.

    Returns:
        Adjusted fill price (higher than mid for buys, lower for sells).

    Raises:
        ValueError: If side is not "buy" or "sell".
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

    # Spread component: half the spread on each side of the mid
    spread_factor = Decimal(str(config.spread_bps)) / Decimal("10000")

    # Market impact: proportional to fraction of daily volume consumed
    if daily_volume > Decimal("0") and config.impact_bps_per_pct > 0:
        volume_fraction_pct = amount / daily_volume * Decimal("100")
        impact_factor = (
            Decimal(str(config.impact_bps_per_pct)) / Decimal("10000") * volume_fraction_pct
        )
    else:
        impact_factor = Decimal("0")

    total_adverse = spread_factor + impact_factor

    if side == "buy":
        return price * (Decimal("1") + total_adverse)
    else:
        return price * (Decimal("1") - total_adverse)
