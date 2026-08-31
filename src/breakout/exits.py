"""
브레이크아웃 페이퍼 포지션의 청산(exit) 판별 로직.

라이브 엔진(RegimeAdaptiveStrategy)의 트레일링 스탑과는 완전히 별개다.
이 모듈은 "막 시작된 큰 움직임을 끝까지 잡는다"는 목적에 맞춰
사용자가 확정한 더 넓은 트레일링 폭(기본 12%, 10~15% 범위)을 쓴다
("트레일링을 좀더 과감하게 조정" → "더 넘게(추천) — 큰 움직임을 끝까지 잡기").

청산 우선순위 (매 틱마다 check_exit()으로 확인):
  1. stop_loss   — 진입 직후엔 base_high 기준 초기 손절선, 신고가 갱신 시
                    trailing_stop_pct 폭으로 끌어올려진(ratchet-only) 값.
  2. time_stop    — time_stop_minutes 안에 한 번도 진입가 위로 트레일링이
                    올라오지 못했다면(=포지션이 전혀 힘을 못 쓰고 있다면) 정리.
                    반대로 이미 수익권으로 트레일링이 올라온 뒤에는 시간 제한을
                    적용하지 않는다 — 큰 움직임을 시간으로 자르지 않기 위함.

네트워크 I/O 없는 순수 로직 — detector.py와 동일한 설계 원칙(단위 테스트 용이).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class ExitConfig:
    """청산 파라미터. trailing_stop_pct 기본값 12.0은 사용자 확정 범위(10~15%)의 중간값."""

    trailing_stop_pct: float = 12.0       # 신고가 대비 이 %만큼 하락하면 청산
    initial_stop_loss_pct: float = 3.0    # 진입가 대비 초기 손절폭(%) — 트레일링이 따라잡기 전 최소 방어선
    time_stop_minutes: int = 60           # 이 시간 안에 수익권 진입 못하면 강제 청산

    def __post_init__(self) -> None:
        if not 0 < self.trailing_stop_pct <= 100:
            raise ValueError("trailing_stop_pct must be within (0, 100]")
        if not 0 < self.initial_stop_loss_pct <= 100:
            raise ValueError("initial_stop_loss_pct must be within (0, 100]")
        if self.time_stop_minutes <= 0:
            raise ValueError("time_stop_minutes must be positive")


@dataclass
class BreakoutPosition:
    """감시 중인 페이퍼 포지션의 가변 상태 (신고가·트레일링 스탑 갱신용)."""

    symbol: str
    entry_price: Decimal
    amount: Decimal
    entry_time: datetime
    base_high: Decimal
    stop_loss: Decimal
    highest_price: Decimal

    @classmethod
    def open_new(
        cls,
        symbol: str,
        entry_price: Decimal,
        amount: Decimal,
        base_high: Decimal,
        cfg: ExitConfig | None = None,
        now: datetime | None = None,
    ) -> "BreakoutPosition":
        cfg = cfg or ExitConfig()
        initial_stop = entry_price * (
            1 - Decimal(str(cfg.initial_stop_loss_pct)) / Decimal("100")
        )
        return cls(
            symbol=symbol,
            entry_price=entry_price,
            amount=amount,
            entry_time=now or datetime.now(UTC),
            base_high=Decimal(str(base_high)),
            stop_loss=initial_stop,
            highest_price=entry_price,
        )


@dataclass(frozen=True)
class ExitSignal:
    reason: Literal["stop_loss", "time_stop"]
    exit_price: Decimal


def check_exit(
    position: BreakoutPosition,
    current_price: Decimal,
    now: datetime | None = None,
    cfg: ExitConfig | None = None,
) -> ExitSignal | None:
    """
    현재가 기준으로 청산 여부를 판단한다. 청산 대상이면 ExitSignal, 아니면 None.

    호출자 계약: None이면 update_trailing()을 호출해 신고가/스탑을 갱신한 뒤
    다음 틱에 반영한다. 청산 신호를 반환한 틱에는 update_trailing()을 호출하지 않는다.
    """
    cfg = cfg or ExitConfig()
    now = now or datetime.now(UTC)

    # 1) 하드 손절 — 초기 방어선이거나, 신고가 갱신으로 끌어올려진 트레일링 스탑
    if current_price <= position.stop_loss:
        return ExitSignal(reason="stop_loss", exit_price=current_price)

    # 2) 타임 스탑 — 정해진 시간 안에 트레일링이 진입가 위로 못 올라왔다면
    #    (=포지션이 아직 수익권에 진입 못했다면) 정리. 이미 수익권으로
    #    트레일링이 올라온 뒤에는 적용하지 않는다 (큰 움직임을 시간으로 자르지 않기 위함).
    elapsed_minutes = (now - position.entry_time).total_seconds() / 60
    if elapsed_minutes >= cfg.time_stop_minutes and position.stop_loss <= position.entry_price:
        return ExitSignal(reason="time_stop", exit_price=current_price)

    return None


def update_trailing(
    position: BreakoutPosition,
    current_price: Decimal,
    cfg: ExitConfig | None = None,
) -> BreakoutPosition:
    """
    신고가 갱신 시 트레일링 스탑을 끌어올린다 (ratchet-only — 절대 내려가지 않음).
    check_exit()이 None을 반환했을 때만 호출할 것.
    """
    cfg = cfg or ExitConfig()
    if current_price <= position.highest_price:
        return position

    trailing_stop = current_price * (
        1 - Decimal(str(cfg.trailing_stop_pct)) / Decimal("100")
    )
    new_stop = max(position.stop_loss, trailing_stop)

    return replace(position, highest_price=current_price, stop_loss=new_stop)


__all__ = ["ExitConfig", "BreakoutPosition", "ExitSignal", "check_exit", "update_trailing"]
