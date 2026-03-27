"""Unit tests for CircuitBreaker."""
from __future__ import annotations

import threading
import time

import pytest

from src.utils.circuit_breaker import CircuitBreaker, State
from src.utils.exceptions import CircuitBreakerOpenError


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_cb(threshold: int = 3, timeout: float = 60.0) -> CircuitBreaker:
    return CircuitBreaker("test", failure_threshold=threshold, recovery_timeout=timeout)


def _fail(cb: CircuitBreaker, n: int = 1) -> None:
    """Trigger n failures on the circuit breaker."""
    for _ in range(n):
        try:
            with cb:
                raise ValueError("boom")
        except ValueError:
            pass


# ── Init validation ───────────────────────────────────────────────────────────

class TestCircuitBreakerInit:
    def test_zero_threshold_raises(self):
        with pytest.raises(ValueError, match="failure_threshold"):
            CircuitBreaker("cb", failure_threshold=0)

    def test_negative_timeout_raises(self):
        with pytest.raises(ValueError, match="recovery_timeout"):
            CircuitBreaker("cb", recovery_timeout=0)

    def test_negative_timeout_raises_negative(self):
        with pytest.raises(ValueError, match="recovery_timeout"):
            CircuitBreaker("cb", recovery_timeout=-1)

    def test_initial_state_is_closed(self):
        cb = _make_cb()
        assert cb.state == State.CLOSED

    def test_initial_failure_count_zero(self):
        cb = _make_cb()
        assert cb.failure_count == 0

    def test_name_stored(self):
        cb = CircuitBreaker("my_exchange")
        assert cb.name == "my_exchange"


# ── CLOSED state ──────────────────────────────────────────────────────────────

class TestClosedState:
    def test_success_does_not_change_state(self):
        cb = _make_cb()
        with cb:
            pass
        assert cb.state == State.CLOSED

    def test_failure_increments_count(self):
        cb = _make_cb(threshold=5)
        _fail(cb, 2)
        assert cb.failure_count == 2
        assert cb.state == State.CLOSED

    def test_success_resets_failure_count(self):
        cb = _make_cb(threshold=5)
        _fail(cb, 2)
        with cb:
            pass  # success
        assert cb.failure_count == 0

    def test_failure_below_threshold_stays_closed(self):
        cb = _make_cb(threshold=3)
        _fail(cb, 2)
        assert cb.state == State.CLOSED

    def test_reaching_threshold_opens_circuit(self):
        cb = _make_cb(threshold=3)
        _fail(cb, 3)
        assert cb.state == State.OPEN

    def test_unexpected_exception_not_counted(self):
        """Only expected_exception types count as failures."""
        cb = CircuitBreaker("cb", failure_threshold=2, expected_exception=ValueError)
        for _ in range(5):
            try:
                with cb:
                    raise TypeError("unexpected")
            except TypeError:
                pass
        assert cb.state == State.CLOSED
        assert cb.failure_count == 0


# ── OPEN state ────────────────────────────────────────────────────────────────

class TestOpenState:
    def test_open_blocks_calls(self):
        cb = _make_cb(threshold=2)
        _fail(cb, 2)
        assert cb.state == State.OPEN
        with pytest.raises(CircuitBreakerOpenError):
            with cb:
                pass  # should never execute

    def test_open_error_includes_name(self):
        cb = _make_cb(threshold=1)
        _fail(cb, 1)
        with pytest.raises(CircuitBreakerOpenError, match="test"):
            with cb:
                pass

    def test_open_does_not_execute_body(self):
        cb = _make_cb(threshold=1)
        _fail(cb)
        executed = []
        try:
            with cb:
                executed.append(True)
        except CircuitBreakerOpenError:
            pass
        assert executed == []


# ── HALF_OPEN state ───────────────────────────────────────────────────────────

class TestHalfOpenState:
    def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker("cb", failure_threshold=1, recovery_timeout=0.05)
        _fail(cb)
        assert cb.state == State.OPEN
        time.sleep(0.1)
        assert cb.state == State.HALF_OPEN

    def test_probe_success_closes_circuit(self):
        cb = CircuitBreaker("cb", failure_threshold=1, recovery_timeout=0.05)
        _fail(cb)
        time.sleep(0.1)
        assert cb.state == State.HALF_OPEN
        with cb:
            pass  # probe succeeds
        assert cb.state == State.CLOSED
        assert cb.failure_count == 0

    def test_probe_failure_reopens_circuit(self):
        cb = CircuitBreaker("cb", failure_threshold=1, recovery_timeout=0.05)
        _fail(cb)
        time.sleep(0.1)
        assert cb.state == State.HALF_OPEN
        try:
            with cb:
                raise ValueError("probe fail")
        except ValueError:
            pass
        assert cb.state == State.OPEN

    def test_re_opened_circuit_blocks_again(self):
        cb = CircuitBreaker("cb", failure_threshold=1, recovery_timeout=0.05)
        _fail(cb)
        time.sleep(0.1)
        # probe fails
        try:
            with cb:
                raise ValueError()
        except ValueError:
            pass
        # now OPEN again
        with pytest.raises(CircuitBreakerOpenError):
            with cb:
                pass


# ── reset() ───────────────────────────────────────────────────────────────────

class TestReset:
    def test_reset_from_open(self):
        cb = _make_cb(threshold=1)
        _fail(cb)
        assert cb.state == State.OPEN
        cb.reset()
        assert cb.state == State.CLOSED
        assert cb.failure_count == 0

    def test_reset_allows_calls_again(self):
        cb = _make_cb(threshold=1)
        _fail(cb)
        cb.reset()
        executed = []
        with cb:
            executed.append(True)
        assert executed == [True]

    def test_reset_from_closed_is_noop(self):
        cb = _make_cb()
        cb.reset()
        assert cb.state == State.CLOSED


# ── call() API ────────────────────────────────────────────────────────────────

class TestCallAPI:
    def test_call_returns_result(self):
        cb = _make_cb()
        result = cb.call(lambda: 42)
        assert result == 42

    def test_call_raises_on_open(self):
        cb = _make_cb(threshold=1)
        _fail(cb)
        with pytest.raises(CircuitBreakerOpenError):
            cb.call(lambda: None)

    def test_call_counts_failure(self):
        cb = _make_cb(threshold=5)
        try:
            cb.call(lambda: (_ for _ in ()).throw(ValueError("x")))
        except ValueError:
            pass
        assert cb.failure_count == 1


# ── Thread safety ─────────────────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_failures_open_circuit(self):
        cb = CircuitBreaker("cb", failure_threshold=10)
        errors = []

        def fail_once():
            try:
                with cb:
                    raise ValueError()
            except (ValueError, CircuitBreakerOpenError):
                pass
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=fail_once) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # After 20 failures (threshold=10), state must be OPEN
        assert cb.state == State.OPEN


# ── Engine integration ────────────────────────────────────────────────────────

class TestEngineCircuitBreaker:
    """Verify the engine exposes and uses a CircuitBreaker."""

    def test_engine_has_circuit_breaker(self):
        from unittest.mock import MagicMock
        from src.engine import TradingEngine

        settings = MagicMock()
        settings.trading_mode = "paper"
        settings.exchange = "binance"
        settings.max_position_risk = 0.02
        settings.max_open_positions = 5
        settings.max_drawdown_halt = 0.15
        settings.max_daily_loss = 0.05

        from src.portfolio.tracker import PortfolioTracker
        from src.risk.manager import PortfolioState, RiskManager
        from decimal import Decimal

        portfolio = PortfolioTracker(initial_cash=Decimal("10000"))
        risk_state = PortfolioState(capital=Decimal("10000"), peak_capital=Decimal("10000"))
        risk_manager = RiskManager(settings, risk_state)

        engine = TradingEngine(
            settings=settings,
            exchange=MagicMock(),
            strategy=MagicMock(),
            risk_manager=risk_manager,
            portfolio=portfolio,
        )
        assert engine.circuit_breaker is not None
        assert engine.circuit_breaker.state == State.CLOSED

    def test_engine_tick_skipped_when_circuit_open(self):
        from unittest.mock import MagicMock, patch
        from src.engine import TradingEngine
        from decimal import Decimal

        settings = MagicMock()
        settings.trading_mode = "paper"
        settings.exchange = "binance"
        settings.max_position_risk = 0.02
        settings.max_open_positions = 5
        settings.max_drawdown_halt = 0.15
        settings.max_daily_loss = 0.05

        from src.portfolio.tracker import PortfolioTracker
        from src.risk.manager import PortfolioState, RiskManager

        portfolio = PortfolioTracker(initial_cash=Decimal("10000"))
        risk_state = PortfolioState(capital=Decimal("10000"), peak_capital=Decimal("10000"))
        risk_manager = RiskManager(settings, risk_state)

        mock_exchange = MagicMock()
        telegram = MagicMock()

        engine = TradingEngine(
            settings=settings,
            exchange=mock_exchange,
            strategy=MagicMock(),
            risk_manager=risk_manager,
            portfolio=portfolio,
            telegram=telegram,
        )

        # Force circuit open
        engine.circuit_breaker.reset()
        for _ in range(5):
            try:
                with engine.circuit_breaker:
                    raise ValueError("force open")
            except ValueError:
                pass

        assert engine.circuit_breaker.state == State.OPEN

        # tick should skip _process_symbol and send alert
        engine.tick("BTC/USDT")
        mock_exchange.get_ohlcv_dataframe.assert_not_called()
        telegram.send_risk_alert.assert_called_once()
