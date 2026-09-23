"""
1d+4h+1h 컨플루언스 BTC 전략의 청산(exit) 판별 로직.

백테스트(multi_tf_confluence_backtest.py, 2026-09-23 — 전체 n=134
PF=1.509 Sharpe=2.611, outlier 점검 통과)와 파라미터·판정 순서를 동일
하게 재현: 손절(ATR) > 상위국면(1d/4h) 이탈 > 최대보유시간. ATR 손절
공식은 RiskManager.calculate_stop_loss의 ATR 분기와 동일하되, 라이브
리스크엔진과 독립된 페이퍼 프로세스라(src/breakout/exits.py,
src/onchain/exits.py와 동일 설계 원칙) 공식만 재현하고 RiskManager는
인스턴스화하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class ConfluenceExitConfig:
    """청산 파라미터. 기본값은 2026-09-23 백테스트에서 검증된 값 그대로."""

    atr_multiplier: float = 2.0
    sl_ceiling_pct: float = 1.0    # 손절 최소거리(%)
    sl_floor_pct: float = 3.0      # 손절 최대거리(%)
    max_hold_hours: int = 20

    def __post_init__(self) -> None:
        if self.atr_multiplier <= 0:
            raise ValueError("atr_multiplier must be positive")
        if not 0 < self.sl_ceiling_pct <= 100:
            raise ValueError("sl_ceiling_pct must be within (0, 100]")
        if not 0 < self.sl_floor_pct <= 100:
            raise ValueError("sl_floor_pct must be within (0, 100]")
        if self.sl_ceiling_pct > self.sl_floor_pct:
            raise ValueError("sl_ceiling_pct (min distance) must be <= sl_floor_pct (max distance)")
        if self.max_hold_hours <= 0:
            raise ValueError("max_hold_hours must be positive")


@dataclass
class ConfluencePosition:
    symbol: str
    entry_price: Decimal
    amount: Decimal
    entry_time: datetime
    stop_loss: Decimal

    @classmethod
    def open_new(
        cls,
        symbol: str,
        entry_price: Decimal,
        amount: Decimal,
        atr_value: float,
        cfg: ConfluenceExitConfig | None = None,
        now: datetime | None = None,
    ) -> "ConfluencePosition":
        cfg = cfg or ConfluenceExitConfig()
        stop_distance = Decimal(str(atr_value * cfg.atr_multiplier))
        raw_stop = max(Decimal("0"), entry_price - stop_distance)
        sl_ceiling = entry_price * (1 - Decimal(str(cfg.sl_ceiling_pct)) / Decimal("100"))
        sl_floor = entry_price * (1 - Decimal(str(cfg.sl_floor_pct)) / Decimal("100"))
        stop_loss = min(max(raw_stop, sl_floor), sl_ceiling)
        return cls(
            symbol=symbol, entry_price=entry_price, amount=amount,
            entry_time=now or datetime.now(UTC), stop_loss=stop_loss,
        )


@dataclass(frozen=True)
class ConfluenceExitSignal:
    reason: Literal["stop_loss", "htf_trend_break", "time_stop"]
    exit_price: Decimal


def check_exit(
    position: ConfluencePosition,
    current_price: Decimal,
    htf_confluence_ok: bool,
    now: datetime | None = None,
    cfg: ConfluenceExitConfig | None = None,
) -> ConfluenceExitSignal | None:
    """판정 순서: 손절 최우선 > 상위국면(1d/4h) 이탈 > 최대보유시간.
    htf_confluence_ok=False면(1d 또는 4h 중 하나라도 uptrend 아님)
    가격과 무관하게 즉시 청산 — 이 전략의 핵심 전제(상위국면 유지) 자체가
    깨졌으므로."""
    cfg = cfg or ConfluenceExitConfig()
    now = now or datetime.now(UTC)

    if current_price <= position.stop_loss:
        return ConfluenceExitSignal(reason="stop_loss", exit_price=current_price)

    if not htf_confluence_ok:
        return ConfluenceExitSignal(reason="htf_trend_break", exit_price=current_price)

    elapsed_hours = (now - position.entry_time).total_seconds() / 3600
    if elapsed_hours >= cfg.max_hold_hours:
        return ConfluenceExitSignal(reason="time_stop", exit_price=current_price)

    return None


__all__ = ["ConfluenceExitConfig", "ConfluencePosition", "ConfluenceExitSignal", "check_exit"]
