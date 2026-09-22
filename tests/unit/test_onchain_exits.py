"""Unit tests for src/onchain/exits.py (BTC 온체인 넷플로우 페이퍼 포지션 청산)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.onchain.exits import OnchainExitConfig, OnchainPosition, check_exit


def _cfg(**overrides) -> OnchainExitConfig:
    base = dict(
        atr_multiplier=2.5, sl_ceiling_pct=3.0, sl_floor_pct=10.0,
        z_exit_threshold=0.0, max_hold_days=30,
    )
    base.update(overrides)
    return OnchainExitConfig(**base)


def _open(entry: str, atr_value: float, cfg=None, now=None) -> OnchainPosition:
    return OnchainPosition.open_new(
        symbol="BTC/KRW",
        entry_price=Decimal(entry),
        amount=Decimal("0.001"),
        atr_value=atr_value,
        cfg=cfg or _cfg(),
        now=now or datetime.now(UTC),
    )


class TestOpenNew:
    def test_atr_stop_within_ceiling_floor_range(self) -> None:
        # ATR*배수 = 100*2.5 = 250, 진입가 100000000 대비 매우 작은 거리라
        # sl_ceiling(3%=최소거리)에 걸려서 3% 손절로 클램프돼야 함
        pos = _open("100000000", atr_value=100.0, cfg=_cfg(sl_ceiling_pct=3.0))
        expected_ceiling = Decimal("100000000") * (1 - Decimal("3.0") / 100)
        assert pos.stop_loss == expected_ceiling

    def test_atr_stop_clamped_to_floor_when_too_wide(self) -> None:
        # ATR*배수가 entry의 10%(sl_floor)보다 훨씬 크면 floor(최대거리)로 클램프
        pos = _open("1000", atr_value=1000.0, cfg=_cfg(sl_floor_pct=10.0))
        expected_floor = Decimal("1000") * (1 - Decimal("10.0") / 100)
        assert pos.stop_loss == expected_floor

    def test_atr_stop_used_as_is_when_within_range(self) -> None:
        cfg = _cfg(sl_ceiling_pct=1.0, sl_floor_pct=20.0)
        pos = _open("1000", atr_value=50.0, cfg=cfg)  # raw stop distance = 125 (12.5%)
        raw_sl = Decimal("1000") - Decimal(str(50.0 * 2.5))
        assert pos.stop_loss == raw_sl


class TestCheckExit:
    def test_no_exit_when_price_holds_and_zscore_negative(self) -> None:
        pos = _open("100", atr_value=1.0)
        assert check_exit(pos, Decimal("101"), current_zscore=-1.0) is None

    def test_stop_loss_triggers_when_price_falls_below_stop(self) -> None:
        pos = _open("100", atr_value=1.0, cfg=_cfg(sl_ceiling_pct=3.0))
        signal = check_exit(pos, Decimal("96"), current_zscore=-1.0)
        assert signal is not None
        assert signal.reason == "stop_loss"
        assert signal.exit_price == Decimal("96")

    def test_signal_exit_when_zscore_reverts_above_threshold(self) -> None:
        pos = _open("100", atr_value=1.0)
        signal = check_exit(pos, Decimal("105"), current_zscore=0.5, cfg=_cfg(z_exit_threshold=0.0))
        assert signal is not None
        assert signal.reason == "signal"
        assert signal.exit_price == Decimal("105")

    def test_no_signal_exit_when_zscore_unknown(self) -> None:
        # z-score 조회 실패(None) 시 신호청산은 보류 -- 손절/타임스탑만 유효
        pos = _open("100", atr_value=1.0)
        assert check_exit(pos, Decimal("105"), current_zscore=None) is None

    def test_time_stop_triggers_after_max_hold_days(self) -> None:
        cfg = _cfg(max_hold_days=30)
        entry_time = datetime.now(UTC) - timedelta(days=31)
        pos = _open("100", atr_value=1.0, cfg=cfg, now=entry_time)
        signal = check_exit(pos, Decimal("101"), current_zscore=-1.0, cfg=cfg)
        assert signal is not None
        assert signal.reason == "time_stop"

    def test_no_time_stop_before_max_hold_days(self) -> None:
        cfg = _cfg(max_hold_days=30)
        entry_time = datetime.now(UTC) - timedelta(days=10)
        pos = _open("100", atr_value=1.0, cfg=cfg, now=entry_time)
        assert check_exit(pos, Decimal("101"), current_zscore=-1.0, cfg=cfg) is None

    def test_stop_loss_takes_priority_over_signal_exit(self) -> None:
        # 같은 틱에 손절가도 뚫리고 z-score도 청산 조건이면 stop_loss가 우선
        # (백테스트 exit_backtest_engine의 체크 순서와 동일한 원칙: 손절이 최우선)
        pos = _open("100", atr_value=1.0, cfg=_cfg(sl_ceiling_pct=3.0))
        signal = check_exit(pos, Decimal("96"), current_zscore=1.0, cfg=_cfg(sl_ceiling_pct=3.0, z_exit_threshold=0.0))
        assert signal is not None
        assert signal.reason == "stop_loss"
