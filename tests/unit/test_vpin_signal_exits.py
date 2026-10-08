"""Unit tests for src/vpin_signal/exits.py (VPIN 신호 페이퍼 포지션 청산)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.vpin_signal.exits import VPINExitConfig, VPINPosition, check_exit


def _cfg(**overrides) -> VPINExitConfig:
    base = dict(atr_multiplier=2.5, sl_ceiling_pct=1.5, sl_floor_pct=5.0,
                tp_rr_multiplier=1.5, max_hold_hours=20.0)
    base.update(overrides)
    return VPINExitConfig(**base)


def _open(entry: str, atr_value: float, cfg=None, now=None) -> VPINPosition:
    return VPINPosition.open_new(
        symbol="BTC/KRW", entry_price=Decimal(entry), amount=Decimal("0.001"),
        atr_value=atr_value, cfg=cfg or _cfg(), now=now or datetime.now(UTC),
    )


class TestConfigValidation:
    def test_ceiling_greater_than_floor_raises(self) -> None:
        with pytest.raises(ValueError):
            VPINExitConfig(sl_ceiling_pct=10.0, sl_floor_pct=5.0)

    def test_zero_max_hold_raises(self) -> None:
        with pytest.raises(ValueError):
            VPINExitConfig(max_hold_hours=0)

    def test_negative_tp_rr_raises(self) -> None:
        with pytest.raises(ValueError):
            VPINExitConfig(tp_rr_multiplier=-1.0)


class TestOpenNew:
    def test_atr_stop_clamped_to_ceiling_when_too_tight(self) -> None:
        pos = _open("100000000", atr_value=100.0, cfg=_cfg(sl_ceiling_pct=1.5))
        expected = Decimal("100000000") * (1 - Decimal("1.5") / 100)
        assert pos.stop_loss == expected

    def test_atr_stop_clamped_to_floor_when_too_wide(self) -> None:
        pos = _open("1000", atr_value=1000.0, cfg=_cfg(sl_floor_pct=5.0))
        expected = Decimal("1000") * (1 - Decimal("5.0") / 100)
        assert pos.stop_loss == expected

    def test_take_profit_sized_as_rr_multiple_of_clamped_sl_distance(self) -> None:
        pos = _open("1000", atr_value=1000.0, cfg=_cfg(sl_floor_pct=5.0, tp_rr_multiplier=1.5))
        sl_dist = Decimal("1000") - pos.stop_loss
        assert pos.take_profit == Decimal("1000") + sl_dist * Decimal("1.5")


class TestCheckExit:
    def test_no_exit_when_price_holds_and_time_not_elapsed(self) -> None:
        now = datetime.now(UTC)
        pos = _open("100", atr_value=1.0, now=now)
        assert check_exit(pos, Decimal("101"), now=now + timedelta(hours=1)) is None

    def test_stop_loss_triggers(self) -> None:
        now = datetime.now(UTC)
        pos = _open("100", atr_value=1.0, cfg=_cfg(sl_ceiling_pct=1.5), now=now)
        signal = check_exit(pos, pos.stop_loss - Decimal("1"), now=now + timedelta(hours=1))
        assert signal is not None and signal.reason == "stop_loss"

    def test_take_profit_triggers(self) -> None:
        now = datetime.now(UTC)
        pos = _open("100", atr_value=1.0, cfg=_cfg(sl_floor_pct=5.0, tp_rr_multiplier=1.5), now=now)
        signal = check_exit(pos, pos.take_profit, now=now + timedelta(hours=1))
        assert signal is not None and signal.reason == "take_profit"

    def test_time_stop_triggers_after_max_hold(self) -> None:
        now = datetime.now(UTC)
        pos = _open("100", atr_value=1.0, cfg=_cfg(max_hold_hours=20.0), now=now)
        signal = check_exit(pos, Decimal("100"), now=now + timedelta(hours=20))
        assert signal is not None and signal.reason == "time_stop"

    def test_no_time_stop_before_max_hold(self) -> None:
        now = datetime.now(UTC)
        pos = _open("100", atr_value=1.0, cfg=_cfg(max_hold_hours=20.0), now=now)
        assert check_exit(pos, Decimal("100"), now=now + timedelta(hours=19, minutes=59)) is None

    def test_stop_loss_takes_priority_over_time_stop(self) -> None:
        now = datetime.now(UTC)
        pos = _open("100", atr_value=1.0, cfg=_cfg(sl_ceiling_pct=1.5, max_hold_hours=20.0), now=now)
        signal = check_exit(pos, pos.stop_loss - Decimal("1"), now=now + timedelta(hours=21))
        assert signal is not None and signal.reason == "stop_loss"

    def test_stop_loss_takes_priority_over_take_profit_when_both_cross(self) -> None:
        # 가격이 손절/익절 둘 다 벗어나는 건 현실적으로 불가능하지만(둘은
        # 반대 방향), 판정 순서 자체(손절 우선)는 코드로 고정돼 있으므로
        # 손절가 자체를 익절가보다 높게 만들어 우선순위를 직접 확인한다.
        now = datetime.now(UTC)
        pos = _open("100", atr_value=1.0, cfg=_cfg(sl_ceiling_pct=1.5, tp_rr_multiplier=1.5), now=now)
        pos.take_profit = pos.stop_loss  # 강제로 같은 값 설정해 우선순위 확인
        signal = check_exit(pos, pos.stop_loss, now=now + timedelta(hours=1))
        assert signal is not None and signal.reason == "stop_loss"
