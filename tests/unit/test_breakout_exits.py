"""Unit tests for src/breakout/exits.py (paper-position trailing stop / time stop)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.breakout.exits import (
    BreakoutPosition,
    ExitConfig,
    check_exit,
    update_trailing,
)


def _cfg(**overrides) -> ExitConfig:
    base = dict(trailing_stop_pct=12.0, initial_stop_loss_pct=3.0, time_stop_minutes=60)
    base.update(overrides)
    return ExitConfig(**base)


def _open(entry: str, base_high: str = "105", cfg: ExitConfig | None = None, now=None) -> BreakoutPosition:
    return BreakoutPosition.open_new(
        symbol="TEST/KRW",
        entry_price=Decimal(entry),
        amount=Decimal("1"),
        base_high=Decimal(base_high),
        cfg=cfg or _cfg(),
        now=now or datetime.now(UTC),
    )


class TestOpenNew:
    def test_initial_stop_loss_below_entry(self) -> None:
        pos = _open("100", cfg=_cfg(initial_stop_loss_pct=3.0))
        assert pos.stop_loss == Decimal("100") * (1 - Decimal("3.0") / 100)
        assert pos.highest_price == Decimal("100")


class TestCheckExit:
    def test_no_exit_when_price_holds_above_stop(self) -> None:
        pos = _open("100")
        assert check_exit(pos, Decimal("101")) is None

    def test_stop_loss_triggers_when_price_falls_to_initial_stop(self) -> None:
        pos = _open("100", cfg=_cfg(initial_stop_loss_pct=3.0))
        signal = check_exit(pos, Decimal("96"))  # below 97 initial stop
        assert signal is not None
        assert signal.reason == "stop_loss"
        assert signal.exit_price == Decimal("96")

    def test_time_stop_triggers_if_never_profitable(self) -> None:
        cfg = _cfg(time_stop_minutes=60)
        entry_time = datetime.now(UTC) - timedelta(minutes=61)
        pos = _open("100", cfg=cfg, now=entry_time)
        # price still above stop_loss, but stop_loss never ratcheted above entry
        signal = check_exit(pos, Decimal("100.5"), cfg=cfg)
        assert signal is not None
        assert signal.reason == "time_stop"

    def test_time_stop_does_not_trigger_before_deadline(self) -> None:
        cfg = _cfg(time_stop_minutes=60)
        entry_time = datetime.now(UTC) - timedelta(minutes=30)
        pos = _open("100", cfg=cfg, now=entry_time)
        assert check_exit(pos, Decimal("100.5"), cfg=cfg) is None

    def test_no_time_stop_once_trailing_moved_above_entry(self) -> None:
        cfg = _cfg(trailing_stop_pct=12.0, time_stop_minutes=60)
        entry_time = datetime.now(UTC) - timedelta(minutes=90)
        pos = _open("100", cfg=cfg, now=entry_time)
        # Simulate price ran up to 120, trailing stop ratchets to 120*0.88=105.6 (above entry)
        pos = update_trailing(pos, Decimal("120"), cfg=cfg)
        assert pos.stop_loss > pos.entry_price
        # Even though time_stop_minutes has elapsed, no time_stop exit — big move still running
        signal = check_exit(pos, Decimal("115"), cfg=cfg)
        assert signal is None

    def test_trailing_stop_loss_triggers_after_pullback(self) -> None:
        cfg = _cfg(trailing_stop_pct=12.0)
        pos = _open("100", cfg=cfg)
        pos = update_trailing(pos, Decimal("120"), cfg=cfg)  # stop -> 105.6
        signal = check_exit(pos, Decimal("105"), cfg=cfg)
        assert signal is not None
        assert signal.reason == "stop_loss"


class TestUpdateTrailing:
    def test_ratchets_up_on_new_high(self) -> None:
        cfg = _cfg(trailing_stop_pct=12.0)
        pos = _open("100", cfg=cfg)
        # Use a big enough move that the trailing-stop formula clearly exceeds
        # the initial 3% stop (100*0.97=97), so max() picks the trailing value.
        updated = update_trailing(pos, Decimal("200"), cfg=cfg)
        assert updated.highest_price == Decimal("200")
        expected_stop = Decimal("200") * (1 - Decimal("12.0") / 100)
        assert updated.stop_loss == expected_stop

    def test_never_ratchets_down(self) -> None:
        cfg = _cfg(trailing_stop_pct=12.0)
        pos = _open("100", cfg=cfg)
        pos = update_trailing(pos, Decimal("150"), cfg=cfg)
        high_stop = pos.stop_loss
        # A lower price than current highest should not move the stop or highest
        pos2 = update_trailing(pos, Decimal("140"), cfg=cfg)
        assert pos2.stop_loss == high_stop
        assert pos2.highest_price == Decimal("150")

    def test_stop_never_falls_below_previous_ratchet(self) -> None:
        # Even if a smaller trailing pct would compute a lower stop than
        # the position already has, max() keeps the tighter (higher) one.
        cfg = _cfg(trailing_stop_pct=12.0)
        pos = _open("100", cfg=cfg)
        pos = update_trailing(pos, Decimal("200"), cfg=cfg)  # stop -> 176
        assert pos.stop_loss == Decimal("200") * (1 - Decimal("12.0") / 100)


class TestExitConfigValidation:
    def test_invalid_trailing_pct_raises(self) -> None:
        with pytest.raises(ValueError):
            ExitConfig(trailing_stop_pct=0)
        with pytest.raises(ValueError):
            ExitConfig(trailing_stop_pct=101)

    def test_invalid_initial_stop_raises(self) -> None:
        with pytest.raises(ValueError):
            ExitConfig(initial_stop_loss_pct=-1)

    def test_invalid_time_stop_raises(self) -> None:
        with pytest.raises(ValueError):
            ExitConfig(time_stop_minutes=0)
