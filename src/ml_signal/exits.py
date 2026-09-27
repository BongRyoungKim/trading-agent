"""
다변량 ML 신호(RandomForest) 페이퍼 포지션의 청산 판별 로직.

백테스트(2026-09-27 리서치, 27개 종목 6구간 워크포워드 — 전 구간 기준선
대비 우위 확인)와 동일하게 **고정 5일 보유 후 청산이 유일한 정상 청산
경로**다 — 백테스트에서 손절 장치를 추가했더니 오히려 성과가 나빠졌음이
확인됐기 때문에(조기청산이 평균회귀가 완성되기 전에 포지션을 끊어버림),
일반 손절선은 의도적으로 넣지 않는다.

다만 `safety_stop_pct`(기본 20%)는 백테스트에 없던 값으로, 극단적
이벤트(거래소 사고, 유동성 위기 등)로부터 페이퍼 자본을 보호하기 위한
안전장치일 뿐 — 평상시 5일 보유 동안 거의 발동하지 않도록 의도적으로
넓게 잡았다(백테스트 재현성보다 안전을 우선한 유일한 이탈 지점, 실거래
전환 시 재검토 필요).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class MLSignalExitConfig:
    hold_days: int = 5             # 백테스트와 동일 — 고정 보유기간이 주 청산 경로
    safety_stop_pct: float = 20.0  # 백테스트에 없던 극단상황 전용 안전장치(모듈 docstring 참고)

    def __post_init__(self) -> None:
        if self.hold_days <= 0:
            raise ValueError("hold_days must be positive")
        if not 0 < self.safety_stop_pct <= 100:
            raise ValueError("safety_stop_pct must be within (0, 100]")


@dataclass
class MLSignalPosition:
    """감시 중인 페이퍼 포지션의 상태."""

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
        cfg: MLSignalExitConfig | None = None,
        now: datetime | None = None,
    ) -> "MLSignalPosition":
        cfg = cfg or MLSignalExitConfig()
        stop_loss = entry_price * (Decimal("1") - Decimal(str(cfg.safety_stop_pct)) / Decimal("100"))
        return cls(
            symbol=symbol,
            entry_price=entry_price,
            amount=amount,
            entry_time=now or datetime.now(UTC),
            stop_loss=stop_loss,
        )


@dataclass(frozen=True)
class MLSignalExitSignal:
    reason: Literal["stop_loss", "time_stop"]
    exit_price: Decimal


def check_exit(
    position: MLSignalPosition,
    current_price: Decimal,
    now: datetime | None = None,
    cfg: MLSignalExitConfig | None = None,
) -> MLSignalExitSignal | None:
    """
    판정 순서(손절 최우선 — 다른 스캐너들과 동일한 원칙):
      1. 안전장치 손절가 이탈 (평상시 거의 발동 안 함)
      2. 고정 보유일수(hold_days) 경과 — 백테스트와 동일한 주 청산 경로
    """
    cfg = cfg or MLSignalExitConfig()
    now = now or datetime.now(UTC)

    if current_price <= position.stop_loss:
        return MLSignalExitSignal(reason="stop_loss", exit_price=current_price)

    elapsed_days = (now - position.entry_time).total_seconds() / 86400
    if elapsed_days >= cfg.hold_days:
        return MLSignalExitSignal(reason="time_stop", exit_price=current_price)

    return None


__all__ = ["MLSignalExitConfig", "MLSignalPosition", "MLSignalExitSignal", "check_exit"]
