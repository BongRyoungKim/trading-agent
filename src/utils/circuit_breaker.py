"""
Circuit Breaker for exchange API calls.

States:
  CLOSED   — normal operation; failures are counted.
  OPEN     — calls are blocked; recovery timer is running.
  HALF_OPEN — one probe call is allowed; success → CLOSED, failure → OPEN.

Usage:
    cb = CircuitBreaker(name="binance", failure_threshold=5, recovery_timeout=60)

    try:
        with cb:
            result = exchange.get_ticker("BTC/USDT")
    except CircuitBreakerOpenError:
        # skip this tick — exchange is down
        ...
"""
from __future__ import annotations

import threading
import time
from enum import Enum, auto
from typing import TYPE_CHECKING

from loguru import logger

from src.utils.exceptions import CircuitBreakerOpenError

if TYPE_CHECKING:
    from types import TracebackType


class State(Enum):
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


class CircuitBreaker:
    """
    Thread-safe circuit breaker.

    Args:
        name:              Identifier used in log messages.
        failure_threshold: Consecutive failures required to open the circuit.
        recovery_timeout:  Seconds to wait in OPEN state before probing.
        expected_exception: Exception type(s) that count as failures.
                            Any other exception propagates normally.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: type[Exception] | tuple[type[Exception], ...] = Exception,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be > 0")

        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._expected_exception = expected_exception

        self._state = State.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> State:
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    # ── Context manager API ───────────────────────────────────────────────────

    def __enter__(self) -> "CircuitBreaker":
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state == State.OPEN:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self._name}' is OPEN",
                    details={
                        "failures": self._failure_count,
                        "recovery_in": self._seconds_until_recovery(),
                    },
                )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: "TracebackType | None",
    ) -> bool:
        if exc_type is None:
            self._on_success()
            return False

        if issubclass(exc_type, self._expected_exception):
            self._on_failure()
            return False  # re-raise

        # Unexpected exception — don't count as a circuit failure
        return False

    # ── Explicit call API ─────────────────────────────────────────────────────

    def call(self, func, *args, **kwargs):
        """Execute *func* inside the circuit breaker."""
        with self:
            return func(*args, **kwargs)

    # ── State transitions ─────────────────────────────────────────────────────

    def _on_success(self) -> None:
        with self._lock:
            if self._state == State.HALF_OPEN:
                logger.info(
                    "Circuit breaker recovered",
                    name=self._name,
                    previous_failures=self._failure_count,
                )
            self._state = State.CLOSED
            self._failure_count = 0
            self._opened_at = None

    def _on_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            if self._state == State.HALF_OPEN:
                # probe failed — stay open
                logger.warning(
                    "Circuit breaker probe failed, staying OPEN",
                    name=self._name,
                    failures=self._failure_count,
                )
                self._state = State.OPEN
                self._opened_at = time.monotonic()
                return

            if self._failure_count >= self._failure_threshold:
                logger.error(
                    "Circuit breaker OPEN",
                    name=self._name,
                    failures=self._failure_count,
                    recovery_timeout=self._recovery_timeout,
                )
                self._state = State.OPEN
                self._opened_at = time.monotonic()

    def _maybe_transition_to_half_open(self) -> None:
        """Called under lock. Transition OPEN → HALF_OPEN if timeout elapsed."""
        if self._state == State.OPEN and self._opened_at is not None:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._recovery_timeout:
                logger.info(
                    "Circuit breaker entering HALF_OPEN for probe",
                    name=self._name,
                    elapsed=round(elapsed, 1),
                )
                self._state = State.HALF_OPEN

    def _seconds_until_recovery(self) -> float:
        if self._opened_at is None:
            return 0.0
        elapsed = time.monotonic() - self._opened_at
        return max(0.0, self._recovery_timeout - elapsed)

    # ── Manual control ────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Force circuit back to CLOSED. Useful for testing or operator override."""
        with self._lock:
            self._state = State.CLOSED
            self._failure_count = 0
            self._opened_at = None
        logger.info("Circuit breaker manually reset", name=self._name)
