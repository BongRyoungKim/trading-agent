"""
Unit tests for src/backtest/data_cache.py.

Only exercises the local-cache read/write path (cache_path, is_cached,
load_cached_ohlcv) — fetch_and_cache_ohlcv()'s network path is not covered
here (it would require mocking ccxt end-to-end for little marginal value;
its cache-hit short-circuit is what these tests actually protect).
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src.backtest import data_cache


@pytest.fixture(autouse=True)
def _isolated_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(data_cache, "CACHE_DIR", tmp_path)
    yield tmp_path


def _write_fake_cache(path, rows) -> None:
    with open(path, "w") as f:
        json.dump(rows, f)


class TestCachePath:
    def test_slash_replaced_with_underscore(self, tmp_path) -> None:
        path = data_cache.cache_path("BTC/KRW", "15m")
        assert path.name == "BTC_KRW_15m.json"
        assert path.parent == tmp_path


class TestIsCached:
    def test_false_when_missing(self) -> None:
        assert data_cache.is_cached("ETH/KRW", "15m") is False

    def test_true_when_present(self, tmp_path) -> None:
        _write_fake_cache(data_cache.cache_path("ETH/KRW", "15m"), [])
        assert data_cache.is_cached("ETH/KRW", "15m") is True


class TestLoadCachedOhlcv:
    def test_raises_when_not_cached(self) -> None:
        with pytest.raises(FileNotFoundError, match="No cached OHLCV"):
            data_cache.load_cached_ohlcv("XRP/KRW", "15m")

    def test_loads_and_parses_columns(self, tmp_path) -> None:
        rows = [
            [1700000000000, 100.0, 105.0, 95.0, 102.0, 10.0],
            [1700000900000, 102.0, 106.0, 101.0, 104.0, 12.0],
        ]
        _write_fake_cache(data_cache.cache_path("XRP/KRW", "15m"), rows)

        df = data_cache.load_cached_ohlcv("XRP/KRW", "15m")

        assert list(df.columns) == ["open", "high", "low", "close", "volume", "timestamp"]
        assert len(df) == 2
        assert df["timestamp"].iloc[0] < df["timestamp"].iloc[1]
        assert isinstance(df["timestamp"].iloc[0], pd.Timestamp)
        assert df["close"].iloc[1] == 104.0

    def test_sorts_out_of_order_rows_by_timestamp(self, tmp_path) -> None:
        rows = [
            [1700000900000, 102.0, 106.0, 101.0, 104.0, 12.0],
            [1700000000000, 100.0, 105.0, 95.0, 102.0, 10.0],
        ]
        _write_fake_cache(data_cache.cache_path("SOL/KRW", "15m"), rows)

        df = data_cache.load_cached_ohlcv("SOL/KRW", "15m")

        assert df["close"].tolist() == [102.0, 104.0]


class TestFetchAndCacheOhlcvSkipsNetworkWhenCached:
    def test_returns_cached_data_without_network_call(self, tmp_path, monkeypatch) -> None:
        rows = [[1700000000000, 1.0, 2.0, 0.5, 1.5, 5.0]]
        _write_fake_cache(data_cache.cache_path("BTC/KRW", "15m"), rows)

        def _boom(*args, **kwargs):
            raise AssertionError("should not hit the network when already cached")

        monkeypatch.setattr(data_cache, "load_cached_ohlcv", data_cache.load_cached_ohlcv)
        import ccxt
        monkeypatch.setattr(ccxt, "upbit", _boom, raising=False)

        from datetime import UTC, datetime
        df = data_cache.fetch_and_cache_ohlcv(
            "BTC/KRW", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC),
        )
        assert len(df) == 1


class TestFetchAndCacheOhlcvFailsFastOnPersistentError:
    """
    실제 사고 재현(2026-09-19~21): 상장폐지/오타 등으로 ccxt.fetch_ohlcv가
    영구적으로 실패하는 심볼을 만나면, 기존 코드는 매 실패마다 2초씩 자며
    무한 재시도했다 — 스크립트가 CPU는 거의 안 쓰면서 며칠씩 아무 진행 없이
    멈춰있는 형태로 나타나 발견이 늦어졌다. 일정 횟수 이상 연속 실패하면
    예외를 그대로 전파하고 멈춰야 한다.
    """

    def test_raises_after_bounded_retries_instead_of_looping_forever(
        self, monkeypatch
    ) -> None:
        import ccxt

        call_count = {"n": 0}

        class _FakeExchange:
            enableRateLimit = False

            def fetch_ohlcv(self, *args, **kwargs):
                call_count["n"] += 1
                raise ccxt.BadSymbol("upbit does not have market symbol FAKE/KRW")

        monkeypatch.setattr(ccxt, "upbit", lambda: _FakeExchange(), raising=False)
        monkeypatch.setattr(data_cache._time, "sleep", lambda *_: None)

        from datetime import UTC, datetime

        with pytest.raises(ccxt.BadSymbol):
            data_cache.fetch_and_cache_ohlcv(
                "FAKE/KRW", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC),
            )

        assert call_count["n"] <= 10, (
            f"기존 버그라면 여기서 무한 루프에 빠져 이 assert에 도달하지 못한다 "
            f"(call_count={call_count['n']})"
        )
