"""
1d+4h+1h 컨플루언스 BTC 전략의 청산(exit) 판별 로직.

2026-09-23 검증: BTC 1h/4h를 2017-10-01까지 확장 수집한 뒤 재검증한
결과, 원안(ATR 기반 손절)보다 트레일링 스탑(초기손절 5% + 신고가 대비
15% 트레일링, ratchet-only)이 **3구간(train n=240, validation n=51,
holdout n=23) 전부에서 우수**(PF/Sharpe 개선, MDD 대폭 개선 — validation
기준 41.24%→18.60%) — 그래서 ATR 기반 로직을 이 트레일링 방식으로
교체한다(multi_tf_confluence_trailing.py, 스크래치패드 검증).

판정 순서(백테스트와 동일): 손절/트레일링 > 상위국면(1d/4h) 이탈 >
최대보유시간. src/breakout/exits.py와 동일한 설계(check_exit이 None을
반환했을 때만 update_trailing 호출 — 청산 신호를 반환한 틱에는 신고가
갱신 안 함).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class ConfluenceExitConfig:
    """청산 파라미터. 기본값은 2026-09-23 재검증(2017-2026, n=240/51/23)에서
    3구간 전부 검증된 값 그대로."""

    initial_stop_pct: float = 5.0     # 진입가 대비 초기 손절폭(%)
    trailing_stop_pct: float = 15.0   # 신고가 대비 트레일링 폭(%)
    max_hold_hours: int = 20

    def __post_init__(self) -> None:
        if not 0 < self.initial_stop_pct <= 100:
            raise ValueError("initial_stop_pct must be within (0, 100]")
        if not 0 < self.trailing_stop_pct <= 100:
            raise ValueError("trailing_stop_pct must be within (0, 100]")
        if self.max_hold_hours <= 0:
            raise ValueError("max_hold_hours must be positive")


@dataclass
class ConfluencePosition:
    """감시 중인 페이퍼 포지션의 가변 상태(신고가·트레일링 스탑 갱신용)."""

    symbol: str
    entry_price: Decimal
    amount: Decimal
    entry_time: datetime
    stop_loss: Decimal
    highest_price: Decimal

    @classmethod
    def open_new(
        cls,
        symbol: str,
        entry_price: Decimal,
        amount: Decimal,
        cfg: ConfluenceExitConfig | None = None,
        now: datetime | None = None,
    ) -> "ConfluencePosition":
        cfg = cfg or ConfluenceExitConfig()
        initial_stop = entry_price * (1 - Decimal(str(cfg.initial_stop_pct)) / Decimal("100"))
        return cls(
            symbol=symbol, entry_price=entry_price, amount=amount,
            entry_time=now or datetime.now(UTC), stop_loss=initial_stop,
            highest_price=entry_price,
        )


@dataclass(frozen=True)
class ConfluenceExitSignal:
    reason: Literal["stop_loss", "htf_trend_break", "time_stop"]
    exit_price: Decimal


def update_trailing(
    position: ConfluencePosition,
    current_price: Decimal,
    cfg: ConfluenceExitConfig | None = None,
) -> ConfluencePosition:
    """신고가 갱신 시 트레일링 스탑을 끌어올린다(ratchet-only — 절대
    내려가지 않음). check_exit()이 None을 반환했을 때만 호출할 것."""
    cfg = cfg or ConfluenceExitConfig()
    if current_price <= position.highest_price:
        return position
    trailing_stop = current_price * (1 - Decimal(str(cfg.trailing_stop_pct)) / Decimal("100"))
    new_stop = max(position.stop_loss, trailing_stop)
    return replace(position, highest_price=current_price, stop_loss=new_stop)


def check_exit(
    position: ConfluencePosition,
    current_price: Decimal,
    htf_confluence_ok: bool,
    now: datetime | None = None,
    cfg: ConfluenceExitConfig | None = None,
) -> ConfluenceExitSignal | None:
    """판정 순서: 손절/트레일링 최우선 > 상위국면(1d/4h) 이탈 > 최대보유시간.
    htf_confluence_ok=False면(1d 또는 4h 중 하나라도 uptrend 아님) 가격과
    무관하게 즉시 청산 — 이 전략의 핵심 전제(상위국면 유지) 자체가
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


__all__ = [
    "ConfluenceExitConfig", "ConfluencePosition", "ConfluenceExitSignal",
    "check_exit", "update_trailing",
]
