"""
BTC 온체인 넷플로우 페이퍼 포지션의 청산(exit) 판별 로직.

백테스트(btc_onchain_netflow_backtest.py, 2026-09-22)와 파라미터·판정
순서를 동일하게 재현: 손절(ATR 기반) > 신호청산(z-score 회복) > 최대보유일수.
ATR 손절 공식은 RiskManager.calculate_stop_loss의 ATR 분기
(entry_price - atr_value*atr_multiplier, 그 후 ceiling/floor 클램프)와
동일하다 — 다만 이 스캐너는 라이브 리스크 엔진(드로다운/포지션한도 등)과
완전히 독립된 페이퍼 프로세스라(src/breakout/exits.py와 같은 설계 원칙)
RiskManager를 통째로 인스턴스화하지 않고 공식만 재현한다.

네트워크 I/O 없는 순수 로직 — src/breakout/exits.py와 동일한 설계 원칙
(단위 테스트 용이).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class OnchainExitConfig:
    """청산 파라미터. 기본값은 2026-09-22 백테스트에서 검증된 값 그대로
    (train n=111 PF=1.102/Sharpe=1.921, validation n=26 PF=1.976/
    Sharpe=4.270, 전체 2017-2026 n=161 PF=1.247/Sharpe=2.102) — 이번
    라운드에서는 재조정하지 않는다(재현성 우선)."""

    atr_multiplier: float = 2.5
    sl_ceiling_pct: float = 3.0    # 손절 최소거리(%) — 너무 타이트한 손절 방지
    sl_floor_pct: float = 10.0     # 손절 최대거리(%) — 최악의 손실 상한
    z_exit_threshold: float = 0.0  # 넷플로우 z-score가 이 값 이상 회복되면 청산
    max_hold_days: int = 30

    def __post_init__(self) -> None:
        if self.atr_multiplier <= 0:
            raise ValueError("atr_multiplier must be positive")
        if not 0 < self.sl_ceiling_pct <= 100:
            raise ValueError("sl_ceiling_pct must be within (0, 100]")
        if not 0 < self.sl_floor_pct <= 100:
            raise ValueError("sl_floor_pct must be within (0, 100]")
        if self.sl_ceiling_pct > self.sl_floor_pct:
            raise ValueError("sl_ceiling_pct (min distance) must be <= sl_floor_pct (max distance)")
        if self.max_hold_days <= 0:
            raise ValueError("max_hold_days must be positive")


@dataclass
class OnchainPosition:
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
        atr_value: float,
        cfg: OnchainExitConfig | None = None,
        now: datetime | None = None,
    ) -> "OnchainPosition":
        cfg = cfg or OnchainExitConfig()
        stop_distance = Decimal(str(atr_value * cfg.atr_multiplier))
        raw_stop = max(Decimal("0"), entry_price - stop_distance)
        sl_ceiling = entry_price * (1 - Decimal(str(cfg.sl_ceiling_pct)) / Decimal("100"))
        sl_floor = entry_price * (1 - Decimal(str(cfg.sl_floor_pct)) / Decimal("100"))
        stop_loss = min(max(raw_stop, sl_floor), sl_ceiling)
        return cls(
            symbol=symbol,
            entry_price=entry_price,
            amount=amount,
            entry_time=now or datetime.now(UTC),
            stop_loss=stop_loss,
        )


@dataclass(frozen=True)
class OnchainExitSignal:
    reason: Literal["stop_loss", "signal", "time_stop"]
    exit_price: Decimal


def check_exit(
    position: OnchainPosition,
    current_price: Decimal,
    current_zscore: float | None,
    now: datetime | None = None,
    cfg: OnchainExitConfig | None = None,
) -> OnchainExitSignal | None:
    """
    판정 순서(백테스트 exit_backtest_engine과 동일한 원칙 — 손절 최우선):
      1. 손절가 이탈
      2. 넷플로우 z-score가 청산 임계값 이상으로 회복(신호반전)
      3. 최대 보유일수 초과

    current_zscore가 None(API 조회 실패 등으로 최신값 미확보)이면 2번은
    건너뛴다 — 불확실한 신호로 청산을 강행하지 않고 손절/타임스탑만 유효.
    """
    cfg = cfg or OnchainExitConfig()
    now = now or datetime.now(UTC)

    if current_price <= position.stop_loss:
        return OnchainExitSignal(reason="stop_loss", exit_price=current_price)

    if current_zscore is not None and current_zscore >= cfg.z_exit_threshold:
        return OnchainExitSignal(reason="signal", exit_price=current_price)

    elapsed_days = (now - position.entry_time).total_seconds() / 86400
    if elapsed_days >= cfg.max_hold_days:
        return OnchainExitSignal(reason="time_stop", exit_price=current_price)

    return None


__all__ = ["OnchainExitConfig", "OnchainPosition", "OnchainExitSignal", "check_exit"]
