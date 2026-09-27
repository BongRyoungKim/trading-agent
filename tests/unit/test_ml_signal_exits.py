"""Unit tests for src/ml_signal/exits.py (다변량 ML 신호 페이퍼 포지션 청산)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.ml_signal.exits import MLSignalExitConfig, MLSignalPosition, check_exit


def _cfg(**overrides) -> MLSignalExitConfig:
    base = dict(hold_days=5, safety_stop_pct=20.0)
    base.update(overrides)
    return MLSignalExitConfig(**base)


def _open(entry: str, cfg=None, now=None) -> MLSignalPosition:
    return MLSignalPosition.open_new(
        symbol="BTC/KRW",
        entry_price=Decimal(entry),
        amount=Decimal("0.001"),
        cfg=cfg or _cfg(),
        now=now or datetime.now(UTC),
    )


class TestConfigValidation:
    def test_zero_hold_days_raises(self) -> None:
        with pytest.raises(ValueError):
            MLSignalExitConfig(hold_days=0)

    def test_negative_safety_stop_raises(self) -> None:
        with pytest.raises(ValueError):
            MLSignalExitConfig(safety_stop_pct=0)

    def test_safety_stop_over_100_raises(self) -> None:
        with pytest.raises(ValueError):
            MLSignalExitConfig(safety_stop_pct=101)


class TestOpenNew:
    def test_stop_loss_is_safety_stop_pct_below_entry(self) -> None:
        pos = _open("1000", cfg=_cfg(safety_stop_pct=20.0))
        assert pos.stop_loss == Decimal("800")

    def test_tighter_safety_stop_pct(self) -> None:
        pos = _open("1000", cfg=_cfg(safety_stop_pct=10.0))
        assert pos.stop_loss == Decimal("900")


class TestCheckExit:
    def test_no_exit_when_price_holds_and_hold_days_not_elapsed(self) -> None:
        now = datetime.now(UTC)
        pos = _open("100", now=now)
        signal = check_exit(pos, Decimal("101"), now=now + timedelta(days=1))
        assert signal is None

    def test_stop_loss_triggers_when_price_breaches_safety_stop(self) -> None:
        now = datetime.now(UTC)
        pos = _open("100", cfg=_cfg(safety_stop_pct=20.0), now=now)
        signal = check_exit(pos, Decimal("79"), now=now + timedelta(hours=1))
        assert signal is not None
        assert signal.reason == "stop_loss"
        assert signal.exit_price == Decimal("79")

    def test_no_stop_loss_when_price_exactly_at_stop(self) -> None:
        # <= 이탈 조건이므로 정확히 stop 가격도 손절 트리거되어야 함
        now = datetime.now(UTC)
        pos = _open("100", cfg=_cfg(safety_stop_pct=20.0), now=now)
        signal = check_exit(pos, Decimal("80"), now=now + timedelta(hours=1))
        assert signal is not None
        assert signal.reason == "stop_loss"

    def test_time_stop_triggers_after_hold_days_elapsed(self) -> None:
        now = datetime.now(UTC)
        pos = _open("100", cfg=_cfg(hold_days=5), now=now)
        signal = check_exit(pos, Decimal("105"), now=now + timedelta(days=5))
        assert signal is not None
        assert signal.reason == "time_stop"
        assert signal.exit_price == Decimal("105")

    def test_no_time_stop_before_hold_days_elapsed(self) -> None:
        now = datetime.now(UTC)
        pos = _open("100", cfg=_cfg(hold_days=5), now=now)
        signal = check_exit(pos, Decimal("105"), now=now + timedelta(days=4, hours=23))
        assert signal is None

    def test_stop_loss_takes_priority_over_time_stop(self) -> None:
        # 보유기간도 지나고 손절가도 이탈 -> 손절이 우선
        now = datetime.now(UTC)
        pos = _open("100", cfg=_cfg(hold_days=5, safety_stop_pct=20.0), now=now)
        signal = check_exit(pos, Decimal("79"), now=now + timedelta(days=6))
        assert signal is not None
        assert signal.reason == "stop_loss"
