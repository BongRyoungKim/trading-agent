"""
Domain exception hierarchy for Trading Agent.
No internal dependencies - safe to import from anywhere.
"""
from __future__ import annotations


class TradingAgentError(Exception):
    """Base exception for all Trading Agent errors."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            detail_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
            return f"{self.message} [{detail_str}]"
        return self.message


# ── Configuration ──────────────────────────────────────────────────────────────

class ConfigurationError(TradingAgentError):
    """Raised when configuration is invalid or missing."""


class MissingCredentialsError(ConfigurationError):
    """Raised when required API credentials are not provided."""


# ── Exchange ───────────────────────────────────────────────────────────────────

class ExchangeError(TradingAgentError):
    """Base class for all exchange-related errors."""


class ConnectionError(ExchangeError):
    """Raised when connection to exchange fails."""


class AuthenticationError(ExchangeError):
    """Raised when exchange API authentication fails."""


class RateLimitError(ExchangeError):
    """Raised when exchange rate limit is exceeded."""


class InsufficientFundsError(ExchangeError):
    """Raised when account has insufficient funds for an order."""


class OrderNotFoundError(ExchangeError):
    """Raised when a requested order does not exist."""


# ── Data ───────────────────────────────────────────────────────────────────────

class DataError(TradingAgentError):
    """Base class for data-related errors."""


class DataFetchError(DataError):
    """Raised when fetching market data fails."""


class DataValidationError(DataError):
    """Raised when market data fails validation checks."""


# ── Strategy ───────────────────────────────────────────────────────────────────

class StrategyError(TradingAgentError):
    """Base class for strategy-related errors."""


class InvalidSignalError(StrategyError):
    """Raised when a strategy generates an invalid trading signal."""


# ── Risk ───────────────────────────────────────────────────────────────────────

class RiskError(TradingAgentError):
    """Base class for risk management errors."""


class MaxDrawdownExceededError(RiskError):
    """Raised when portfolio drawdown exceeds the configured halt threshold."""


class PositionLimitExceededError(RiskError):
    """Raised when attempting to open more positions than the configured limit."""


class TradingCooldownError(RiskError):
    """
    Raised when new entries are temporarily paused by the consecutive-loss
    circuit breaker. Unlike other RiskError subclasses this is not an
    operational violation — it is an expected, self-clearing pause that ends
    once the cooldown window elapses.
    """


# ── Backtest ───────────────────────────────────────────────────────────────────

class BacktestError(TradingAgentError):
    """Base class for backtesting errors."""


class InsufficientDataError(BacktestError):
    """Raised when there is not enough historical data to run a backtest."""


# ── Circuit Breaker ────────────────────────────────────────────────────────────

class CircuitBreakerOpenError(TradingAgentError):
    """Raised when a call is blocked because the circuit breaker is OPEN."""
