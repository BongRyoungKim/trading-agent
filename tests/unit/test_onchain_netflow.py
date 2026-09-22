"""Unit tests for src/onchain/netflow.py (온체인 넷플로우 z-score 순수 계산)."""
from __future__ import annotations

import pandas as pd
import pytest

from src.onchain.netflow import compute_zscore


def _df(netflows: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(netflows), freq="D").date
    return pd.DataFrame({"date": dates, "netflow": netflows})


class TestComputeZscore:
    def test_returns_none_when_fewer_than_lookback_rows(self) -> None:
        df = _df([1.0] * 10)
        assert compute_zscore(df, lookback=30) is None

    def test_returns_none_when_std_is_zero(self) -> None:
        # 30일 내내 완전히 동일한 값이면 표준편차 0 -> z-score 정의 불가
        df = _df([5.0] * 30)
        assert compute_zscore(df, lookback=30) is None

    def test_strong_net_outflow_gives_large_negative_zscore(self) -> None:
        # 29일은 평범한 유입(100), 마지막 하루만 극단적 순유출(-500)
        netflows = [100.0] * 29 + [-500.0]
        df = _df(netflows)
        z = compute_zscore(df, lookback=30)
        assert z is not None
        assert z < -1.5

    def test_strong_net_inflow_gives_large_positive_zscore(self) -> None:
        netflows = [-100.0] * 29 + [800.0]
        df = _df(netflows)
        z = compute_zscore(df, lookback=30)
        assert z is not None
        assert z > 1.5

    def test_typical_day_gives_zscore_near_zero(self) -> None:
        # 마지막 값도 나머지와 비슷한 분포 -> z-score가 0 근처
        netflows = [10.0, -5.0, 8.0, -3.0, 6.0] * 6
        df = _df(netflows)
        z = compute_zscore(df, lookback=30)
        assert z is not None
        assert abs(z) < 2.0

    def test_uses_last_lookback_rows_only(self) -> None:
        # 앞부분에 극단값이 있어도 lookback 윈도우 밖이면 영향 없어야 함
        netflows = [100000.0] * 5 + [10.0, -5.0, 8.0, -3.0, 6.0] * 6
        df = _df(netflows)
        z_full = compute_zscore(df, lookback=30)
        z_without_outlier = compute_zscore(df.iloc[5:], lookback=30)
        assert z_full == pytest.approx(z_without_outlier)
