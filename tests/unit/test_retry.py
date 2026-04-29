"""Unit tests for src/utils/retry.py"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.utils.exceptions import AuthenticationError, RateLimitError
from src.utils.retry import retry


class TestRetrySync:
    def test_succeeds_on_first_attempt(self) -> None:
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01)
        def func() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        result = func()
        assert result == "ok"
        assert call_count == 1

    def test_retries_on_rate_limit_error(self) -> None:
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01, jitter=False)
        def func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RateLimitError("rate limited")
            return "ok"

        with patch("time.sleep"):
            result = func()

        assert result == "ok"
        assert call_count == 3

    def test_raises_after_max_attempts(self) -> None:
        @retry(max_attempts=3, base_delay=0.01, jitter=False)
        def func() -> None:
            raise RateLimitError("always rate limited")

        with patch("time.sleep"), pytest.raises(RateLimitError):
            func()

    def test_does_not_retry_non_retryable_exception(self) -> None:
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01)
        def func() -> None:
            nonlocal call_count
            call_count += 1
            raise AuthenticationError("bad key")

        with pytest.raises(AuthenticationError):
            func()

        assert call_count == 1

    def test_respects_max_delay(self) -> None:
        sleep_calls: list[float] = []

        @retry(max_attempts=4, base_delay=10.0, max_delay=5.0, jitter=False)
        def func() -> None:
            raise RateLimitError("rate limited")

        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            with pytest.raises(RateLimitError):
                func()

        assert all(s <= 5.0 for s in sleep_calls)


class TestRetryAsync:
    @pytest.mark.asyncio
    async def test_async_succeeds_on_first_attempt(self) -> None:
        @retry(max_attempts=3, base_delay=0.01)
        async def func() -> str:
            return "ok"

        result = await func()
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_async_retries_on_rate_limit(self) -> None:
        call_count = 0

        @retry(max_attempts=3, base_delay=0.001, jitter=False)
        async def func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RateLimitError("rate limited")
            return "ok"

        result = await func()
        assert result == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_async_raises_after_max_attempts(self) -> None:
        @retry(max_attempts=3, base_delay=0.001, jitter=False)
        async def func() -> None:
            raise RateLimitError("always fails")

        with pytest.raises(RateLimitError):
            await func()

    @pytest.mark.asyncio
    async def test_async_jitter_applies(self) -> None:
        call_count = 0

        @retry(max_attempts=3, base_delay=0.001, jitter=True)
        async def func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RateLimitError("rate limited")
            return "ok"

        result = await func()
        assert result == "ok"

    def test_sync_jitter_applies(self) -> None:
        call_count = 0

        @retry(max_attempts=3, base_delay=0.001, jitter=True)
        def func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RateLimitError("rate limited")
            return "ok"

        with patch("time.sleep"):
            result = func()
        assert result == "ok"
