"""
Retry decorator with exponential backoff for exchange API calls.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

from loguru import logger

from src.utils.exceptions import RateLimitError, TradingAgentError

F = TypeVar("F", bound=Callable[..., Any])

# Exceptions that should trigger a retry
_RETRYABLE = (RateLimitError,)


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
) -> Callable[[F], F]:
    """
    Decorator for retrying a function on retryable exceptions.

    Args:
        max_attempts: Maximum number of total attempts (including first try).
        base_delay: Initial delay in seconds before first retry.
        max_delay: Maximum delay cap in seconds.
        backoff_factor: Multiplier applied to delay after each attempt.
        jitter: Add random jitter (±25%) to avoid thundering herd.

    Example:
        @retry(max_attempts=3, base_delay=1.0)
        def fetch_ticker(symbol: str) -> Ticker: ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = base_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except _RETRYABLE as exc:
                    if attempt == max_attempts:
                        logger.error(
                            "Max retries reached",
                            func=func.__name__,
                            attempts=attempt,
                            error=str(exc),
                        )
                        raise
                    sleep_time = min(delay, max_delay)
                    if jitter:
                        sleep_time *= 1 + random.uniform(-0.25, 0.25)
                    logger.warning(
                        "Retryable error, backing off",
                        func=func.__name__,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        sleep_seconds=round(sleep_time, 2),
                        error=str(exc),
                    )
                    time.sleep(sleep_time)
                    delay = min(delay * backoff_factor, max_delay)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = base_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except _RETRYABLE as exc:
                    if attempt == max_attempts:
                        logger.error(
                            "Max retries reached",
                            func=func.__name__,
                            attempts=attempt,
                            error=str(exc),
                        )
                        raise
                    sleep_time = min(delay, max_delay)
                    if jitter:
                        sleep_time *= 1 + random.uniform(-0.25, 0.25)
                    logger.warning(
                        "Retryable error, backing off",
                        func=func.__name__,
                        attempt=attempt,
                        sleep_seconds=round(sleep_time, 2),
                    )
                    await asyncio.sleep(sleep_time)
                    delay = min(delay * backoff_factor, max_delay)

        if inspect.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]

    return decorator
