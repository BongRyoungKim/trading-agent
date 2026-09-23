"""Unit tests for src/confluence/exits.py (1d+4h+1h 컨플루언스 포지션 청산).

2026-09-23 검증 결과(원안 ATR손절 대신 트레일링 스탑이 3구간(train n=240,
validation n=51, holdout n=23) 전부에서 우수 — PF/Sharpe 개선, MDD
대폭 개선)로 손절 로직을 ATR 기반에서 트레일링 스탑(breakout scanner와
동일 패턴)으로 교체.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.confluence.exits import ConfluenceExitConfig, ConfluencePosition, check_exit, update_trailing


def _cfg(**overrides) -> ConfluenceExitConfig:
    base = dict(initial_stop_pct=5.0, trailing_stop_pct=15.0, max_hold_hours=20)
    base.update(overrides)
    return ConfluenceExitConfig(**base)


def _open(entry: str, cfg=None, now=None) -> ConfluencePosition:
    return ConfluencePosition.open_new(
        symbol="BTC/KRW", entry_price=Decimal(entry), amount=Decimal("0.001"),
        cfg=cfg or _cfg(), now=now or datetime.now(UTC),
    )


class TestOpenNew:
    def test_initial_stop_below_entry_by_initial_pct(self) -> None:
        pos = _open("100", cfg=_cfg(initial_stop_pct=5.0))
        assert pos.stop_loss == Decimal("100") * (1 - Decimal("5.0") / 100)
        assert pos.highest_price == Decimal("100")


class TestUpdateTrailing:
    def test_no_change_when_price_not_new_high(self) -> None:
        pos = _open("100")
        updated = update_trailing(pos, Decimal("99"))
        assert updated.highest_price == Decimal("100")
        assert updated.stop_loss == pos.stop_loss

    def test_ratchets_stop_up_on_new_high(self) -> None:
        cfg = _cfg(trailing_stop_pct=15.0)
        pos = _open("100", cfg=cfg)
        updated = update_trailing(pos, Decimal("120"), cfg=cfg)
        assert updated.highest_price == Decimal("120")
        assert updated.stop_loss == Decimal("120") * (1 - Decimal("15.0") / 100)

    def test_stop_never_moves_down(self) -> None:
        cfg = _cfg(initial_stop_pct=5.0, trailing_stop_pct=15.0)
        pos = _open("100", cfg=cfg)
        raised = update_trailing(pos, Decimal("200"), cfg=cfg)
        # 신고가 200 -> 트레일링스탑 200*0.85=170, 기존 초기손절(95)보다 위로 올라감
        assert raised.stop_loss == Decimal("170")
        # 그 다음 봉에서 가격이 신고가를 못 넘기면 스탑은 그대로 유지(하락 안 함)
        held = update_trailing(raised, Decimal("150"), cfg=cfg)
        assert held.stop_loss == Decimal("170")


class TestCheckExit:
    def test_no_exit_when_price_holds_and_confluence_ok(self) -> None:
        pos = _open("100")
        assert check_exit(pos, Decimal("101"), htf_confluence_ok=True) is None

    def test_stop_loss_triggers(self) -> None:
        pos = _open("100", cfg=_cfg(initial_stop_pct=5.0))
        signal = check_exit(pos, Decimal("94"), htf_confluence_ok=True)
        assert signal is not None
        assert signal.reason == "stop_loss"

    def test_htf_trend_break_triggers_even_if_price_ok(self) -> None:
        pos = _open("100")
        signal = check_exit(pos, Decimal("105"), htf_confluence_ok=False)
        assert signal is not None
        assert signal.reason == "htf_trend_break"

    def test_time_stop_triggers_after_max_hold(self) -> None:
        cfg = _cfg(max_hold_hours=20)
        entry_time = datetime.now(UTC) - timedelta(hours=21)
        pos = _open("100", cfg=cfg, now=entry_time)
        signal = check_exit(pos, Decimal("101"), htf_confluence_ok=True, cfg=cfg)
        assert signal is not None
        assert signal.reason == "time_stop"

    def test_no_time_stop_before_max_hold(self) -> None:
        cfg = _cfg(max_hold_hours=20)
        entry_time = datetime.now(UTC) - timedelta(hours=5)
        pos = _open("100", cfg=cfg, now=entry_time)
        assert check_exit(pos, Decimal("101"), htf_confluence_ok=True, cfg=cfg) is None

    def test_stop_loss_takes_priority_over_trend_break(self) -> None:
        pos = _open("100", cfg=_cfg(initial_stop_pct=5.0))
        signal = check_exit(pos, Decimal("94"), htf_confluence_ok=False)
        assert signal is not None
        assert signal.reason == "stop_loss"
