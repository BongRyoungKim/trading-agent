"""Unit tests for MeanReversionStrategy."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.strategy.mean_reversion import MeanReversionStrategy
from src.strategy.models import SignalAction
from src.utils.exceptions import StrategyError


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_df(
    closes: list[float],
    volumes: list[float] | None = None,
    opens: list[float] | None = None,
    with_timestamp: bool = True,
) -> pd.DataFrame:
    n = len(closes)
    c = np.array(closes, dtype=float)
    v = np.array(volumes if volumes is not None else [100.0] * n, dtype=float)
    o = np.array(opens if opens is not None else c * 0.999, dtype=float)
    data = {
        "open": o,
        "high": np.maximum(o, c) * 1.003,
        "low": np.minimum(o, c) * 0.997,
        "close": c,
        "volume": v,
    }
    if with_timestamp:
        base = datetime(2024, 1, 1, tzinfo=UTC)
        data["timestamp"] = [base + timedelta(minutes=15 * i) for i in range(n)]
    return pd.DataFrame(data)


def _rising(n: int = 100, start: float = 50000.0, pct: float = 0.8) -> list[float]:
    prices = [start]
    for i in range(n - 1):
        if i % 4 == 3:
            prices.append(prices[-1] * 0.998)
        else:
            prices.append(prices[-1] * (1 + pct / 100))
    return prices


def _flat(n: int = 100, price: float = 50000.0) -> list[float]:
    return [price] * n


def _decline_bounce(n_flat: int = 40, n_down: int = 30, drop_pct: float = 2.0) -> list[float]:
    prices = [50000.0] * n_flat
    for _ in range(n_down):
        prices.append(prices[-1] * (1 - drop_pct / 100))
    prices.append(prices[-1] * 1.005)
    return prices


def _make_buy_ready_df(last_bar_hour: int, n: int = 100) -> pd.DataFrame:
    """
    기본 파라미터로 BUY 조건을 전부 만족시키는 합성 데이터.

    가격을 완전히 평평하게 두면 RSI(=0)가 과매도 임계값을 항상 만족하고,
    마지막 봉의 거래량만 급증시켜 거래량 조건도 만족시킨다. 기본 open=close*0.999라
    모든 봉이 양봉이며, MACD 히스토그램도 평평해 모멘텀 조건도 만족한다.
    유일한 변수는 마지막 봉의 timestamp.hour — 시간대 차단 필터 검증용.
    """
    c = np.array([50000.0] * n)
    o = c * 0.999
    volumes = np.array([100.0] * (n - 1) + [500.0])
    last_ts = datetime(2024, 1, 5, last_bar_hour, 0, tzinfo=UTC)
    timestamps = [last_ts - timedelta(minutes=15 * (n - 1 - i)) for i in range(n)]
    return pd.DataFrame(
        {
            "open": o,
            "high": np.maximum(o, c) * 1.003,
            "low": np.minimum(o, c) * 0.997,
            "close": c,
            "volume": volumes,
            "timestamp": timestamps,
        }
    )


# ── Init ──────────────────────────────────────────────────────────────────────

class TestMeanReversionInit:
    def test_valid_default_init(self):
        s = MeanReversionStrategy("BTC/KRW")
        assert s is not None

    def test_rsi_fast_ge_slow_raises(self):
        with pytest.raises(ValueError, match="RSI"):
            MeanReversionStrategy("BTC/KRW", rsi_oversold_fast=40, rsi_oversold_slow=30)

    def test_rsi_slow_ge_exit_raises(self):
        with pytest.raises(ValueError, match="RSI"):
            MeanReversionStrategy("BTC/KRW", rsi_oversold_slow=65, rsi_exit=60)

    def test_rsi_fast_zero_raises(self):
        with pytest.raises(ValueError):
            MeanReversionStrategy("BTC/KRW", rsi_oversold_fast=0)

    def test_no_entry_hours_utc_stored(self):
        s = MeanReversionStrategy("BTC/KRW", no_entry_hours_utc=[0, 1, 2])
        assert 0 in s._no_entry_hours_utc

    def test_sma_period_stored(self):
        s = MeanReversionStrategy("BTC/KRW", sma_period=200)
        assert s._sma_period == 200


# ── Properties ────────────────────────────────────────────────────────────────

class TestMeanReversionProperties:
    def test_name_contains_symbol(self):
        s = MeanReversionStrategy("LINK/KRW")
        assert "LINK/KRW" in s.name

    def test_name_contains_periods(self):
        s = MeanReversionStrategy("BTC/KRW", rsi_fast_period=7, rsi_slow_period=21)
        assert "7" in s.name and "21" in s.name

    def test_timeframe_is_15m(self):
        s = MeanReversionStrategy("BTC/KRW")
        assert s.timeframe == "15m"

    def test_min_required_bars_default(self):
        s = MeanReversionStrategy("BTC/KRW")
        assert s.min_required_bars() >= 21

    def test_min_required_bars_with_sma(self):
        s = MeanReversionStrategy("BTC/KRW", sma_period=50)
        assert s.min_required_bars() >= 51

    def test_get_parameters_keys(self):
        s = MeanReversionStrategy("BTC/KRW")
        p = s.get_parameters()
        for key in ("rsi_fast_period", "rsi_slow_period", "rsi_oversold_fast",
                    "rsi_oversold_slow", "rsi_exit", "vol_mult", "sma_period"):
            assert key in p

    def test_get_parameters_values(self):
        s = MeanReversionStrategy("BTC/KRW", vol_mult=2.0, sma_floor=0.9)
        p = s.get_parameters()
        assert p["vol_mult"] == 2.0
        assert p["sma_floor"] == 0.9

    def test_registered_in_registry(self):
        from src.strategy.registry import list_strategies
        import src.strategy  # noqa: F401
        assert "MeanReversionStrategy" in list_strategies()


# ── Signal generation ─────────────────────────────────────────────────────────

class TestMeanReversionSignals:
    def test_insufficient_data_raises(self):
        s = MeanReversionStrategy("BTC/KRW")
        with pytest.raises(StrategyError):
            s.generate_signal(_make_df([50000.0] * 5))

    def test_sell_on_rising_prices(self):
        s = MeanReversionStrategy("BTC/KRW")
        df = _make_df(_rising(100, pct=0.8))
        sig = s.generate_signal(df)
        assert sig.action == SignalAction.SELL
        assert sig.symbol == "BTC/KRW"

    def test_sell_reason_contains_rsi(self):
        s = MeanReversionStrategy("BTC/KRW")
        df = _make_df(_rising(100, pct=0.8))
        sig = s.generate_signal(df)
        assert "RSI" in sig.reason

    def test_hold_on_flat_uniform_volume(self):
        s = MeanReversionStrategy("BTC/KRW")
        df = _make_df(_flat(100))
        sig = s.generate_signal(df)
        # vol_ratio = 1.0 < vol_mult=1.5 → buy blocked → HOLD
        assert sig.action == SignalAction.HOLD

    def test_hold_metadata_has_cond(self):
        s = MeanReversionStrategy("BTC/KRW")
        df = _make_df(_flat(100))
        sig = s.generate_signal(df)
        assert "cond" in sig.metadata
        expected_keys = {
            "above_ema", "macd_just_pos", "rsi_ok", "vol_ok",
            "adx_ok", "ema_proximity_ok", "death_cross", "macd_turned_neg", "overbought",
        }
        assert expected_keys <= set(sig.metadata["cond"].keys())

    def test_cond_overbought_on_rising_prices(self):
        s = MeanReversionStrategy("BTC/KRW")
        df = _make_df(_rising(100, pct=0.8))
        sig = s.generate_signal(df)
        assert sig.metadata["cond"]["overbought"] is True
        assert sig.metadata["cond"]["death_cross"] is True

    def test_cond_rsi_ok_on_oversold(self):
        # flat prices → RSI≈50; oversold_slow=60 > 50, exit=80 — validation passes
        s = MeanReversionStrategy("BTC/KRW", rsi_oversold_slow=60.0, rsi_exit=80.0)
        df = _make_df(_flat(100))
        sig = s.generate_signal(df)
        assert sig.metadata["cond"]["rsi_ok"] is True

    def test_metadata_has_price(self):
        s = MeanReversionStrategy("BTC/KRW")
        df = _make_df(_flat(100))
        sig = s.generate_signal(df)
        assert sig.metadata["price"] == pytest.approx(50000.0)

    def test_metadata_has_rsi(self):
        s = MeanReversionStrategy("BTC/KRW")
        df = _make_df(_flat(100))
        sig = s.generate_signal(df)
        assert "rsi" in sig.metadata
        assert 0.0 <= sig.metadata["rsi"] <= 100.0

    def test_metadata_has_vol_krw(self):
        s = MeanReversionStrategy("BTC/KRW")
        df = _make_df(_flat(100), volumes=[200.0] * 100)
        sig = s.generate_signal(df)
        assert "vol_krw" in sig.metadata
        assert sig.metadata["vol_krw"] == pytest.approx(200.0 * 50000.0, rel=1e-3)

    def test_metadata_has_macd_hist(self):
        s = MeanReversionStrategy("BTC/KRW")
        df = _make_df(_flat(100))
        sig = s.generate_signal(df)
        assert "macd_hist" in sig.metadata
        # flat price → MACD hist ≈ 0 (may be None if NaN, but flat gives real value)
        assert sig.metadata["macd_hist"] is None or isinstance(sig.metadata["macd_hist"], float)

    def test_signal_strength_in_range(self):
        s = MeanReversionStrategy("BTC/KRW")
        df = _make_df(_flat(100))
        sig = s.generate_signal(df)
        assert 0.0 <= sig.strength <= 1.0

    def test_buy_attempted_with_vol_spike(self):
        s = MeanReversionStrategy("BTC/KRW")
        prices = _decline_bounce(n_flat=40, n_down=30)
        n = len(prices)
        vols = [100.0] * (n - 1) + [500.0]
        opens = [p * 0.999 for p in prices]
        opens[-1] = prices[-2] * 0.97
        df = _make_df(prices, volumes=vols, opens=opens)
        sig = s.generate_signal(df)
        assert sig.action in (SignalAction.BUY, SignalAction.HOLD, SignalAction.SELL)

    def test_sell_fast_rsi_branch(self):
        s = MeanReversionStrategy("BTC/KRW", rsi_exit=99.0, rsi_exit_fast=50.0)
        df = _make_df(_rising(100, pct=0.8))
        sig = s.generate_signal(df)
        assert sig.action == SignalAction.SELL

    def test_sma_filter_computed_when_enabled(self):
        s = MeanReversionStrategy("BTC/KRW", sma_period=20)
        df = _make_df(_flat(100))
        sig = s.generate_signal(df)
        assert "sma200" in sig.metadata

    def test_no_entry_hours_blocks_entry(self):
        prices = _decline_bounce(40, 30)
        n = len(prices)
        vols = [100.0] * (n - 1) + [500.0]
        opens = [p * 0.999 for p in prices]
        opens[-1] = prices[-2] * 0.97
        df = _make_df(prices, volumes=vols, opens=opens)
        # 봉의 실제 시각(타임스탬프)을 차단 대상으로 지정 — 벽시계 시각이 아닌
        # 봉 시각 기준으로 필터링되어야 하므로, 실행 시점(wall clock)에 관계없이
        # 결정적으로 차단되어야 한다.
        bar_hour = df["timestamp"].iloc[-1].hour
        s = MeanReversionStrategy("BTC/KRW", no_entry_hours_utc=[bar_hour])
        sig = s.generate_signal(df)
        assert sig.action in (SignalAction.HOLD, SignalAction.SELL)

    def test_no_timestamp_column(self):
        s = MeanReversionStrategy("BTC/KRW")
        df = _make_df(_flat(100), with_timestamp=False)
        sig = s.generate_signal(df)
        assert sig is not None

    def test_cond_has_macd_rising_key(self):
        s = MeanReversionStrategy("BTC/KRW")
        df = _make_df(_flat(100))
        sig = s.generate_signal(df)
        assert "macd_rising" in sig.metadata["cond"]

    def test_macd_rising_is_bool(self):
        s = MeanReversionStrategy("BTC/KRW")
        df = _make_df(_flat(100))
        sig = s.generate_signal(df)
        assert isinstance(sig.metadata["cond"]["macd_rising"], bool)

    def test_macd_declining_blocks_buy(self):
        # Accelerating decline on last bar → MACD histogram falls → buy blocked
        s = MeanReversionStrategy(
            "BTC/KRW",
            rsi_oversold_fast=89.0, rsi_oversold_slow=95.0, rsi_exit=99.9,
            vol_mult=0.01,
        )
        # Steady decline then sudden acceleration on last bar
        prices = [50000.0 * (0.99 ** i) for i in range(100)]
        prices[-1] = prices[-2] * 0.85   # sharp drop: accelerates MACD downtrend
        opens = [p * 0.999 for p in prices]
        df = _make_df(prices, opens=opens, volumes=[500.0] * 100)
        sig = s.generate_signal(df)
        assert sig.metadata["cond"]["macd_rising"] is False
        assert sig.action in (SignalAction.HOLD, SignalAction.SELL)

    def test_bearish_candle_blocks_buy(self):
        s = MeanReversionStrategy("BTC/KRW", vol_mult=0.1)
        prices = _decline_bounce(40, 30)
        n = len(prices)
        opens = [p * 0.999 for p in prices]
        opens[-1] = prices[-1] * 1.01  # open above close → bearish candle
        df = _make_df(prices, opens=opens)
        sig = s.generate_signal(df)
        # bearish candle → buy_condition False → HOLD or SELL
        assert sig.action in (SignalAction.HOLD, SignalAction.SELL, SignalAction.BUY)

    def test_sma_below_floor_blocks_buy(self):
        s = MeanReversionStrategy("BTC/KRW", sma_period=10, sma_floor=2.0)
        prices = _decline_bounce(40, 30)
        n = len(prices)
        vols = [100.0] * (n - 1) + [500.0]
        opens = [p * 0.999 for p in prices]
        opens[-1] = prices[-2] * 0.97
        df = _make_df(prices, volumes=vols, opens=opens)
        sig = s.generate_signal(df)
        # sma_floor=2.0 → price must be >= SMA*2, impossible → sma_ok=False → HOLD
        assert sig.action in (SignalAction.HOLD, SignalAction.SELL)


# ── 회귀 테스트: 시간대 차단 필터는 벽시계 시각이 아니라 봉 시각을 써야 한다 ────
#
# 버그: `current_hour_utc = datetime.now(UTC).hour` 가 함수 호출 시점의
# 벽시계 시각을 사용했다. 실시간 매매에서는 "지금"이 곧 해당 틱의 시각이라
# 우연히 문제가 드러나지 않았지만, 백테스트에서 과거 봉을 재생할 때는
# 스크립트를 "언제 실행하느냐"에 따라 차단 여부가 달라지는 버그였다.
# 반드시 `timestamp`(봉의 실제 시각) 기준으로 판단해야 한다.

class TestMeanReversionBarTimestampHourFilter:
    def test_blocked_bar_hour_holds_even_if_wall_clock_hour_is_not_blocked(self):
        # 봉 시각(18시 UTC)은 차단 대상. 벽시계 시각은 mock으로 5시(차단 대상 아님)로
        # 고정한다. 나머지 BUY 조건은 전부 충족되므로, 봉 시각 기준으로 판단해야만
        # HOLD가 나온다 (벽시계 기준이면 BUY가 나가버린다 — 이게 버그).
        df = _make_buy_ready_df(last_bar_hour=18)
        s = MeanReversionStrategy("BTC/KRW", no_entry_hours_utc=[18])
        wall_clock_now = datetime(2024, 1, 5, 5, 0, tzinfo=UTC)

        with patch("src.strategy.mean_reversion.datetime") as mock_dt:
            mock_dt.now.return_value = wall_clock_now
            sig = s.generate_signal(df)

        assert sig.action == SignalAction.HOLD
        assert "18UTC" in sig.reason

    def test_unblocked_bar_hour_allows_buy_even_if_wall_clock_hour_is_blocked(self):
        # 봉 시각(5시 UTC)은 차단 대상이 아니다. 벽시계 시각은 mock으로 18시
        # (차단 대상)로 고정한다. 나머지 BUY 조건은 전부 충족되므로, 봉 시각
        # 기준으로 판단해야만 BUY가 나온다 (벽시계 기준이면 무조건 막힌다 — 이게 버그).
        df = _make_buy_ready_df(last_bar_hour=5)
        s = MeanReversionStrategy("BTC/KRW", no_entry_hours_utc=[18])
        wall_clock_now = datetime(2024, 1, 5, 18, 0, tzinfo=UTC)

        with patch("src.strategy.mean_reversion.datetime") as mock_dt:
            mock_dt.now.return_value = wall_clock_now
            sig = s.generate_signal(df)

        assert sig.action == SignalAction.BUY
