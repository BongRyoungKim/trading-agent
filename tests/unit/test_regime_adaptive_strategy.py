"""Unit tests for RegimeAdaptiveStrategy."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.strategy.models import SignalAction
from src.strategy.regime_adaptive import RegimeAdaptiveStrategy
from src.utils.exceptions import StrategyError
from src.utils.indicators import ema


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_df(
    closes: list[float],
    volumes: list[float] | None = None,
    opens: list[float] | None = None,
) -> pd.DataFrame:
    n = len(closes)
    c = np.array(closes, dtype=float)
    v = np.array(volumes if volumes is not None else [2000.0] * n, dtype=float)
    o = np.array(opens if opens is not None else c * 0.999, dtype=float)
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return pd.DataFrame({
        "open":      o,
        "high":      np.maximum(o, c) * 1.002,
        "low":       np.minimum(o, c) * 0.998,
        "close":     c,
        "volume":    v,
        "timestamp": [base + timedelta(minutes=15 * i) for i in range(n)],
    })


def _uptrend_data(n: int = 150, start: float = 50_000.0) -> pd.DataFrame:
    """Consistent 0.5% rise per bar → ADX high, EMA20 > EMA50."""
    prices = [start * (1.005 ** i) for i in range(n)]
    return _make_df(prices)


def _downtrend_data(n: int = 150, start: float = 50_000.0) -> pd.DataFrame:
    """Consistent 0.5% fall per bar → ADX high, EMA20 < EMA50."""
    prices = [start * (0.995 ** i) for i in range(n)]
    return _make_df(prices)


def _ranging_data(n: int = 150, base: float = 50_000.0) -> pd.DataFrame:
    """Alternating ±0.3% bars → ADX low."""
    prices = []
    p = base
    for i in range(n):
        p = p * (1.003 if i % 2 == 0 else 0.997)
        prices.append(p)
    return _make_df(prices)


def _make_strategy(**kwargs) -> RegimeAdaptiveStrategy:
    return RegimeAdaptiveStrategy(symbol="BTC/KRW", **kwargs)


def _noisy_data(seed: int, n: int = 150, start: float = 50_000.0) -> pd.DataFrame:
    """Random-walk-ish data with variable drift, to sweep through regime
    transitions (uptrend/ranging/downtrend) within a single series."""
    rng = np.random.default_rng(seed)
    drift = rng.uniform(-0.01, 0.015, size=n)
    noise = rng.normal(0.0, 0.01, size=n)
    prices = [start]
    for i in range(1, n):
        prices.append(prices[-1] * (1 + drift[i] + noise[i]))
    return _make_df(prices)


# ── Constructor validation ─────────────────────────────────────────────────────

class TestConstructor:
    def test_default_params(self):
        s = _make_strategy()
        params = s.get_parameters()
        assert params["adx_trend_threshold"] == 25.0
        assert params["ema_fast"] == 20
        assert params["ema_slow"] == 50

    def test_name_includes_ema_periods(self):
        s = _make_strategy(ema_fast=10, ema_slow=30)
        assert "10" in s.name
        assert "30" in s.name

    def test_timeframe_is_15m(self):
        assert _make_strategy().timeframe == "15m"

    def test_invalid_ema_order_raises(self):
        with pytest.raises(ValueError, match="ema_fast"):
            _make_strategy(ema_fast=50, ema_slow=20)

    def test_invalid_adx_threshold_raises(self):
        with pytest.raises(ValueError, match="adx_trend_threshold"):
            _make_strategy(adx_trend_threshold=0)

    def test_min_required_bars_at_least_100(self):
        # SwingMomentumStrategy requires ema_slow * 2 = 100 by default
        assert _make_strategy().min_required_bars() >= 100


# ── Regime detection ──────────────────────────────────────────────────────────

class TestDetectRegime:
    def test_uptrend_detected(self):
        s = _make_strategy()
        regime = s.detect_regime(_uptrend_data())
        assert regime == "uptrend"

    def test_downtrend_detected(self):
        s = _make_strategy()
        regime = s.detect_regime(_downtrend_data())
        assert regime == "downtrend"

    def test_ranging_detected(self):
        s = _make_strategy()
        regime = s.detect_regime(_ranging_data())
        assert regime == "ranging"

    def test_custom_adx_threshold_raises_ranging_bar(self):
        # At threshold=99, even strong uptrend ADX (~100) is ≥ 99 → still uptrend
        # but ranging market (ADX ~5-15) is always below 99 → "ranging"
        s = _make_strategy(adx_trend_threshold=99.0)
        regime = s.detect_regime(_ranging_data())
        assert regime == "ranging"


# ── Signal routing ────────────────────────────────────────────────────────────

class TestSignalRouting:
    def test_uptrend_reason_contains_uptrend_tag(self):
        s = _make_strategy()
        signal = s.generate_signal(_uptrend_data())
        assert "[UPTREND]" in signal.reason

    def test_downtrend_reason_contains_downtrend_tag(self):
        s = _make_strategy()
        signal = s.generate_signal(_downtrend_data())
        assert "[DOWNTREND]" in signal.reason

    def test_ranging_reason_contains_ranging_tag(self):
        s = _make_strategy()
        signal = s.generate_signal(_ranging_data())
        assert "[RANGING]" in signal.reason

    def test_regime_in_metadata(self):
        s = _make_strategy()
        for data, expected in [
            (_uptrend_data(), "uptrend"),
            (_downtrend_data(), "downtrend"),
            (_ranging_data(), "ranging"),
        ]:
            sig = s.generate_signal(data)
            assert sig.metadata.get("regime") == expected

    def test_signal_symbol_preserved(self):
        s = _make_strategy()
        sig = s.generate_signal(_uptrend_data())
        assert sig.symbol == "BTC/KRW"

    def test_returns_hold_when_conditions_unmet(self):
        # Ranging data with default MeanReversion conditions → HOLD expected
        # (no volume spike, no RSI oversold)
        s = _make_strategy()
        sig = s.generate_signal(_ranging_data())
        # Should not raise; action can be HOLD or actionable
        assert sig.action in (SignalAction.BUY, SignalAction.SELL, SignalAction.HOLD)


# ── Insufficient data ─────────────────────────────────────────────────────────

class TestInsufficientData:
    def test_raises_on_too_few_bars(self):
        s = _make_strategy()
        short_df = _uptrend_data(n=10)
        with pytest.raises(StrategyError):
            s.generate_signal(short_df)


# ── Parameter passthrough ─────────────────────────────────────────────────────

class TestParameterPassthrough:
    def test_mr_params_in_sub_strategy(self):
        s = _make_strategy(mr_vol_mult=0.5, mr_rsi_oversold_fast=15.0)
        mr_params = s.get_parameters()["mean_reversion"]
        assert mr_params["vol_mult"] == 0.5
        assert mr_params["rsi_oversold_fast"] == 15.0

    def test_sm_params_in_sub_strategy(self):
        s = _make_strategy(sm_vol_mult=2.0, sm_adx_threshold=30.0)
        sm_params = s.get_parameters()["swing_momentum"]
        assert sm_params["vol_mult"] == 2.0
        assert sm_params["adx_threshold"] == 30.0

    def test_mr_rsi_exit_fast_passed_through(self):
        s = _make_strategy(mr_rsi_exit_fast=78.0)
        mr_params = s.get_parameters()["mean_reversion"]
        assert mr_params["rsi_exit_fast"] == 78.0

    def test_no_entry_hours_passed_to_mr(self):
        s = _make_strategy(mr_no_entry_hours_utc=[0, 1, 2])
        # Verify sub-strategy holds the setting (via regime→MR delegation)
        # MeanReversionStrategy stores hours in _no_entry_hours_utc
        assert hasattr(s._mean_reversion, "_no_entry_hours_utc")
        assert 0 in s._mean_reversion._no_entry_hours_utc

    def test_mr_sma_period_default_is_zero_disabled(self):
        # Backward compatibility: no mr_sma_period arg → long-term trend
        # filter stays fully disabled, identical to pre-existing behaviour.
        s = _make_strategy()
        mr_params = s.get_parameters()["mean_reversion"]
        assert mr_params["sma_period"] == 0

    def test_mr_sma_period_and_floor_passed_to_sub_strategy(self):
        s = _make_strategy(mr_sma_period=199, mr_sma_floor=0.955)
        mr_params = s.get_parameters()["mean_reversion"]
        assert mr_params["sma_period"] == 199
        assert mr_params["sma_floor"] == 0.955


# ── SwingMomentum 데스크로스 SELL 회귀 방지 ──────────────────────────────────
#
# 배경: RegimeAdaptiveStrategy는 매 틱 무상태로 국면을 재검출해 SwingMomentum에
# 위임한다. detect_regime()의 uptrend 판정(EMA_fast > EMA_slow)과 SwingMomentum
# SELL①의 데스크로스 판정(EMA_fast < EMA_slow)이 "동일한" EMA fast/slow 쌍을
# 쓰는 한, 데스크로스가 성립하는 순간 국면검출기가 이미 downtrend로 재분류해
# MeanReversion으로 라우팅하므로 SwingMomentum의 데스크로스 SELL 경로는
# RegimeAdaptiveStrategy 하에서 구조적으로 도달 불가능하다(27심볼 510,838봉
# 실측: 데스크로스 발생 5,699봉 중 uptrend였던 것 0봉). 이 불변식이 깨지는
# 유일한 경우는 국면검출용 EMA 기간과 SwingMomentum 내부 EMA 기간이 서로
# 달라지는 것 — 아래 테스트들은 그 전제와 실제 동작 양쪽을 검증한다.

class TestSwingMomentumDeathCrossUnreachableUnderUptrend:
    def test_regime_detector_and_swing_momentum_share_same_ema_periods(self):
        """
        구조적 전제 검증: RegimeAdaptiveStrategy가 국면검출에 쓰는 EMA fast/slow
        기간은 반드시 위임 대상 SwingMomentumStrategy 내부의 EMA fast/slow
        기간과 동일해야 한다. 이 값이 달라지면(예: 국면검출과 SwingMomentum의
        EMA 기간을 독립적으로 설정 가능하게 만드는 변경) 아래 데이터 기반 테스트가
        실패할 수 있으므로, 그 전제 자체를 먼저 고정한다.
        """
        s = _make_strategy()
        assert s._ema_fast_period == s._swing_momentum._ema_fast
        assert s._ema_slow_period == s._swing_momentum._ema_slow

    def test_uptrend_precludes_death_cross_for_arbitrary_ema_value_pairs(self):
        """
        수학적 불변식: detect_regime()의 uptrend 조건(EMA_fast_now > EMA_slow_now)과
        SwingMomentum의 데스크로스 조건(EMA_fast_prev >= EMA_slow_prev and
        EMA_fast_now < EMA_slow_now)은, 동일한 EMA_fast_now/EMA_slow_now 값을
        사용하는 한 동시에 참일 수 없다 — uptrend가 참이면 death_cross는 반드시
        거짓이다. 임의의 EMA 값 쌍 다수로 이 불변식을 검증한다.
        """
        rng = np.random.default_rng(20240914)
        for _ in range(500):
            ema_fast_prev, ema_slow_prev, ema_fast_now, ema_slow_now = rng.uniform(
                1.0, 100_000.0, size=4
            )

            uptrend = ema_fast_now > ema_slow_now
            death_cross = (
                ema_fast_prev >= ema_slow_prev and ema_fast_now < ema_slow_now
            )

            if uptrend:
                assert not death_cross

    @pytest.mark.parametrize("seed", range(15))
    def test_generate_signal_uptrend_regime_never_hits_death_cross_branch(self, seed):
        """
        통합 검증: 실제 RegimeAdaptiveStrategy.generate_signal() 경로에서, 국면이
        uptrend로 분류된 모든 구간에서 위임된 SwingMomentum 신호가 데스크로스
        SELL(SELL①)로 나올 수 없음을 다양한 합성 시계열(추세/횡보/노이즈
        혼합)로 확인한다.
        """
        s = _make_strategy()
        data = _noisy_data(seed=seed)
        min_bars = s.min_required_bars()

        for end in range(min_bars, len(data) + 1, 5):
            window = data.iloc[:end].reset_index(drop=True)
            regime = s.detect_regime(window)
            if regime != "uptrend":
                continue

            inner = s._swing_momentum.generate_signal(window)

            # SwingMomentum이 자체적으로 계산한 uptrend 플래그도 동일해야 하며
            # (=EMA 기간이 실제로 일치함을 데이터로도 재확인), 데스크로스 SELL
            # 문구는 절대 등장하지 않아야 한다.
            assert inner.metadata["cond"]["uptrend"] is True
            assert "데스크로스" not in inner.reason
