"""
VPIN(방향성 거래량 불균형) 신호 페이퍼 포지션의 청산 판별 로직.

2026-10-08 리서치(12개 종목, 8개월을 6등분한 구간별 분해 — 6구간 중 4구간
플러스, 최근 구간 특히 강함)와 파라미터·판정 순서를 동일하게 재현:
ATR 손절(ceiling/floor 클램프) > RR 기반 목표가(익절) > 최대보유시간.
ATR은 Upbit 가격(진입할 실제 시장)에서 계산 — 신호 자체는 바이낸스
테이커 매수/매도 비율로 계산되지만, 포지션 리스크는 실제 체결 시장
기준이어야 하므로 구분한다(research script의 backtest_signal()과 동일
원칙).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class VPINExitConfig:
    """기본값은 2026-10-08 리서치에서 검증된 값 그대로(window=40, q=0.9
    조합 기준) — 재현성 우선, 이번 라운드에서 재조정하지 않는다."""

    atr_multiplier: float = 2.5
    sl_ceiling_pct: float = 1.5    # 손절 최소거리(%) — 너무 타이트한 손절 방지
    sl_floor_pct: float = 5.0      # 손절 최대거리(%) — 최악의 손실 상한
    tp_rr_multiplier: float = 1.5  # 손절거리 대비 목표가 배수
    max_hold_hours: float = 20.0   # 백테스트 max_hold_bars=80 (15분×80)과 동일

    def __post_init__(self) -> None:
        if self.atr_multiplier <= 0:
            raise ValueError("atr_multiplier must be positive")
        if not 0 < self.sl_ceiling_pct <= 100:
            raise ValueError("sl_ceiling_pct must be within (0, 100]")
        if not 0 < self.sl_floor_pct <= 100:
            raise ValueError("sl_floor_pct must be within (0, 100]")
        if self.sl_ceiling_pct > self.sl_floor_pct:
            raise ValueError("sl_ceiling_pct (min distance) must be <= sl_floor_pct (max distance)")
        if self.tp_rr_multiplier <= 0:
            raise ValueError("tp_rr_multiplier must be positive")
        if self.max_hold_hours <= 0:
            raise ValueError("max_hold_hours must be positive")


@dataclass
class VPINPosition:
    symbol: str
    entry_price: Decimal
    amount: Decimal
    entry_time: datetime
    stop_loss: Decimal
    take_profit: Decimal

    @classmethod
    def open_new(
        cls,
        symbol: str,
        entry_price: Decimal,
        amount: Decimal,
        atr_value: float,
        cfg: VPINExitConfig | None = None,
        now: datetime | None = None,
    ) -> "VPINPosition":
        cfg = cfg or VPINExitConfig()
        stop_distance = Decimal(str(atr_value * cfg.atr_multiplier))
        raw_stop = max(Decimal("0"), entry_price - stop_distance)
        sl_ceiling = entry_price * (1 - Decimal(str(cfg.sl_ceiling_pct)) / Decimal("100"))
        sl_floor = entry_price * (1 - Decimal(str(cfg.sl_floor_pct)) / Decimal("100"))
        stop_loss = min(max(raw_stop, sl_floor), sl_ceiling)
        sl_dist = entry_price - stop_loss
        take_profit = entry_price + sl_dist * Decimal(str(cfg.tp_rr_multiplier))
        return cls(
            symbol=symbol, entry_price=entry_price, amount=amount,
            entry_time=now or datetime.now(UTC), stop_loss=stop_loss, take_profit=take_profit,
        )


@dataclass(frozen=True)
class VPINExitSignal:
    reason: Literal["stop_loss", "take_profit", "time_stop"]
    exit_price: Decimal


def check_exit(
    position: VPINPosition,
    current_price: Decimal,
    now: datetime | None = None,
    cfg: VPINExitConfig | None = None,
) -> VPINExitSignal | None:
    """판정 순서(손절 최우선, 백테스트의 backtest_signal()과 동일):
    1. 손절가 이탈  2. 목표가(RR) 도달  3. 최대 보유시간 초과"""
    cfg = cfg or VPINExitConfig()
    now = now or datetime.now(UTC)

    if current_price <= position.stop_loss:
        return VPINExitSignal(reason="stop_loss", exit_price=current_price)
    if current_price >= position.take_profit:
        return VPINExitSignal(reason="take_profit", exit_price=current_price)

    elapsed_hours = (now - position.entry_time).total_seconds() / 3600
    if elapsed_hours >= cfg.max_hold_hours:
        return VPINExitSignal(reason="time_stop", exit_price=current_price)

    return None


__all__ = ["VPINExitConfig", "VPINPosition", "VPINExitSignal", "check_exit"]
