"""Unit tests for src/confluence/exits.py (1d+4h+1h 컨플루언스 포지션 청산)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.confluence.exits import ConfluenceExitConfig, ConfluencePosition, check_exit


def _cfg(**overrides) -> ConfluenceExitConfig:
    base = dict(atr_multiplier=2.0, sl_ceiling_pct=1.0, sl_floor_pct=3.0, max_hold_hours=20)
    base.update(overrides)
    return ConfluenceExitConfig(**base)


def _open(entry: str, atr_value: float, cfg=None, now=None) -> ConfluencePosition:
    return ConfluencePosition.open_new(
        symbol="BTC/KRW", entry_price=Decimal(entry), amount=Decimal("0.001"),
        atr_value=atr_value, cfg=cfg or _cfg(), now=now or datetime.now(UTC),
    )


class TestOpenNew:
    def test_stop_clamped_to_ceiling_when_atr_too_tight(self) -> None:
        pos = _open("100000000", atr_value=100.0, cfg=_cfg(sl_ceiling_pct=1.0))
        assert pos.stop_loss == Decimal("100000000") * (1 - Decimal("1.0") / 100)

    def test_stop_clamped_to_floor_when_atr_too_wide(self) -> None:
        pos = _open("1000", atr_value=1000.0, cfg=_cfg(sl_floor_pct=3.0))
        assert pos.stop_loss == Decimal("1000") * (1 - Decimal("3.0") / 100)


class TestCheckExit:
    def test_no_exit_when_price_holds_and_confluence_ok(self) -> None:
        pos = _open("100", atr_value=1.0)
        assert check_exit(pos, Decimal("101"), htf_confluence_ok=True) is None

    def test_stop_loss_triggers(self) -> None:
        pos = _open("100", atr_value=1.0, cfg=_cfg(sl_ceiling_pct=1.0))
        signal = check_exit(pos, Decimal("98"), htf_confluence_ok=True)
        assert signal is not None
        assert signal.reason == "stop_loss"

    def test_htf_trend_break_triggers_even_if_price_ok(self) -> None:
        pos = _open("100", atr_value=1.0)
        signal = check_exit(pos, Decimal("105"), htf_confluence_ok=False)
        assert signal is not None
        assert signal.reason == "htf_trend_break"

    def test_time_stop_triggers_after_max_hold(self) -> None:
        cfg = _cfg(max_hold_hours=20)
        entry_time = datetime.now(UTC) - timedelta(hours=21)
        pos = _open("100", atr_value=1.0, cfg=cfg, now=entry_time)
        signal = check_exit(pos, Decimal("101"), htf_confluence_ok=True, cfg=cfg)
        assert signal is not None
        assert signal.reason == "time_stop"

    def test_no_time_stop_before_max_hold(self) -> None:
        cfg = _cfg(max_hold_hours=20)
        entry_time = datetime.now(UTC) - timedelta(hours=5)
        pos = _open("100", atr_value=1.0, cfg=cfg, now=entry_time)
        assert check_exit(pos, Decimal("101"), htf_confluence_ok=True, cfg=cfg) is None

    def test_stop_loss_takes_priority_over_trend_break(self) -> None:
        pos = _open("100", atr_value=1.0, cfg=_cfg(sl_ceiling_pct=1.0))
        signal = check_exit(pos, Decimal("98"), htf_confluence_ok=False)
        assert signal is not None
        assert signal.reason == "stop_loss"
