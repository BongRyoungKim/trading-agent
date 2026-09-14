"""
Counterfactual position-sizing replay over an already-realised trade sequence.

Answers "what would the equity path have looked like if each entry had been
sized at X% of equity instead of the 25% that was actually used?" by taking
the *realised* per-trade net return (which is sizing-invariant — it is a
percentage of notional) and re-applying a different sizing rule to the same
entry/exit timeline.

Constraints reproduced from the live engine (src/engine.py:802-839):
  - size = position_size_pct x total equity (cash + cost basis of open
    positions), not cash alone;
  - notional capped at cash_cap_ratio (99%) of free cash;
  - orders below min_order_krw are bumped *up* to the minimum, and only
    skipped when even the capped cash cannot reach that minimum;
  - concurrent positions capped at max_open_positions
    (RiskManager.check_position_limit).

Documented limitations (see docs/research-protocol.md):
  - Open positions are marked at cost, not at market — the journal has no
    intra-trade prices — so drawdown only registers at exit. Portfolio MDD
    from this replay is a LOWER BOUND on the true intraday drawdown.
  - Entries that never happened live (skipped by the slot limit, the
    consecutive-loss cooldown, or a cash shortfall) are absent from the
    journal and cannot be recovered, so a smaller sizing can never gain
    trades in this replay.
  - Fill prices are assumed unchanged at larger order sizes (no slippage
    model).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Iterable, Mapping, Sequence


class SkipReason(str, Enum):
    """Why a historical entry could not be taken under the replayed sizing."""

    SLOT_LIMIT = "slot_limit"
    INSUFFICIENT_CASH = "insufficient_cash"


@dataclass(frozen=True)
class ReplayTrade:
    """One realised trade, reduced to what sizing replay needs."""

    symbol: str
    entry_time: datetime
    exit_time: datetime
    return_pct: float  # net of commission, as a fraction (0.02 = +2%)


@dataclass(frozen=True)
class SizingConfig:
    """Sizing rule + the live engine's cash/slot constraints."""

    initial_capital: Decimal
    position_size_pct: float
    max_open_positions: int = 4
    min_order_krw: Decimal = Decimal("10000")
    cash_cap_ratio: Decimal = Decimal("0.99")

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError(f"initial_capital must be positive, got {self.initial_capital}")
        if not 0.0 < self.position_size_pct <= 1.0:
            raise ValueError(
                f"position_size_pct must be in (0.0, 1.0], got {self.position_size_pct}"
            )
        if self.max_open_positions <= 0:
            raise ValueError(
                f"max_open_positions must be positive, got {self.max_open_positions}"
            )
        if self.min_order_krw < 0:
            raise ValueError(f"min_order_krw must be >= 0, got {self.min_order_krw}")
        if not Decimal("0") < self.cash_cap_ratio <= Decimal("1"):
            raise ValueError(f"cash_cap_ratio must be in (0, 1], got {self.cash_cap_ratio}")


@dataclass(frozen=True)
class ExecutedTrade:
    """A historical trade as it would have been taken under the new sizing."""

    symbol: str
    entry_time: datetime
    exit_time: datetime
    size: Decimal            # notional committed at entry
    pnl: Decimal             # net PnL in currency under this sizing
    equity_at_entry: Decimal


@dataclass(frozen=True)
class SkippedTrade:
    symbol: str
    entry_time: datetime
    reason: SkipReason


@dataclass(frozen=True)
class EquityPoint:
    timestamp: datetime
    equity: Decimal          # cash + cost basis of open positions


@dataclass(frozen=True)
class ReplayResult:
    config: SizingConfig
    final_capital: Decimal
    executed: tuple[ExecutedTrade, ...]
    skipped: tuple[SkippedTrade, ...]
    equity_points: tuple[EquityPoint, ...]

    @property
    def total_return_pct(self) -> float:
        return float(
            (self.final_capital - self.config.initial_capital)
            / self.config.initial_capital
            * 100
        )


@dataclass(frozen=True)
class _OpenSlot:
    exit_time: datetime
    size: Decimal
    return_pct: float

    @property
    def proceeds(self) -> Decimal:
        return self.size * (Decimal("1") + Decimal(str(self.return_pct)))


def to_replay_trades(rows: Iterable[Mapping[str, object]]) -> list[ReplayTrade]:
    """
    Convert journal.db rows (see src/portfolio/journal_store.py's schema) into
    ReplayTrade objects. Each row needs symbol, amount, entry_price, pnl and
    epoch-second entry_time/exit_time.

    Raises:
        ValueError: if a row's notional (amount x entry_price) is zero, which
            would make its percentage return undefined.
    """
    trades: list[ReplayTrade] = []
    for row in rows:
        notional = Decimal(str(row["amount"])) * Decimal(str(row["entry_price"]))
        if notional == 0:
            raise ValueError(f"Zero notional for trade row: {row!r}")
        trades.append(
            ReplayTrade(
                symbol=str(row["symbol"]),
                entry_time=datetime.fromtimestamp(float(row["entry_time"]), tz=UTC),  # type: ignore[arg-type]
                exit_time=datetime.fromtimestamp(float(row["exit_time"]), tz=UTC),  # type: ignore[arg-type]
                return_pct=float(Decimal(str(row["pnl"])) / notional),
            )
        )
    return trades


def _close_due_slots(
    slots: list[_OpenSlot],
    now: datetime,
    cash: Decimal,
    points: list[EquityPoint],
) -> tuple[list[_OpenSlot], Decimal]:
    """Settle every open slot whose exit_time has passed. Returns (slots, cash)."""
    due = sorted((s for s in slots if s.exit_time <= now), key=lambda s: s.exit_time)
    remaining = [s for s in slots if s.exit_time > now]
    # Positions that are due but not yet settled in this loop still hold their
    # cost basis — counting only `remaining` would fake a drawdown whenever
    # several positions are settled in one batch.
    unsettled_cost = sum((s.size for s in due), start=Decimal("0"))
    remaining_cost = sum((s.size for s in remaining), start=Decimal("0"))
    for slot in due:
        cash += slot.proceeds
        unsettled_cost -= slot.size
        points.append(
            EquityPoint(
                timestamp=slot.exit_time,
                equity=cash + unsettled_cost + remaining_cost,
            )
        )
    return remaining, cash


def _decide_size(
    equity: Decimal, cash: Decimal, config: SizingConfig
) -> Decimal | None:
    """Return the notional to commit, or None if the order cannot be placed."""
    max_notional = cash * config.cash_cap_ratio
    if max_notional < config.min_order_krw:
        return None
    size = min(equity * Decimal(str(config.position_size_pct)), max_notional)
    return max(size, config.min_order_krw)


def replay_with_sizing(
    trades: Sequence[ReplayTrade], config: SizingConfig
) -> ReplayResult:
    """
    Replay `trades` under `config`'s sizing rule.

    Trades are processed in entry_time order; positions are settled at their
    recorded exit_time. See the module docstring for the limitations this
    inherits from replaying a realised journal.
    """
    cash = config.initial_capital
    slots: list[_OpenSlot] = []
    executed: list[ExecutedTrade] = []
    skipped: list[SkippedTrade] = []
    points: list[EquityPoint] = []

    for trade in sorted(trades, key=lambda t: (t.entry_time, t.symbol)):
        slots, cash = _close_due_slots(slots, trade.entry_time, cash, points)
        open_cost = sum((s.size for s in slots), start=Decimal("0"))
        equity = cash + open_cost

        if len(slots) >= config.max_open_positions:
            skipped.append(SkippedTrade(trade.symbol, trade.entry_time, SkipReason.SLOT_LIMIT))
            continue

        size = _decide_size(equity, cash, config)
        if size is None:
            skipped.append(
                SkippedTrade(trade.symbol, trade.entry_time, SkipReason.INSUFFICIENT_CASH)
            )
            continue

        cash -= size
        slots.append(_OpenSlot(trade.exit_time, size, trade.return_pct))
        executed.append(
            ExecutedTrade(
                symbol=trade.symbol,
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                size=size,
                pnl=size * Decimal(str(trade.return_pct)),
                equity_at_entry=equity,
            )
        )
        points.append(EquityPoint(timestamp=trade.entry_time, equity=equity))

    still_open = sorted(slots, key=lambda s: s.exit_time)
    for i, slot in enumerate(still_open):
        cash += slot.proceeds
        open_cost = sum((s.size for s in still_open[i + 1 :]), start=Decimal("0"))
        points.append(EquityPoint(timestamp=slot.exit_time, equity=cash + open_cost))

    return ReplayResult(
        config=config,
        final_capital=cash,
        executed=tuple(executed),
        skipped=tuple(skipped),
        equity_points=tuple(points),
    )
