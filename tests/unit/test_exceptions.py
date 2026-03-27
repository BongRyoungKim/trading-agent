"""Unit tests for src/utils/exceptions.py"""
import pytest

from src.utils.exceptions import (
    AuthenticationError,
    BacktestError,
    ConfigurationError,
    ConnectionError,
    DataError,
    DataFetchError,
    DataValidationError,
    ExchangeError,
    InsufficientDataError,
    InsufficientFundsError,
    InvalidSignalError,
    MaxDrawdownExceededError,
    MissingCredentialsError,
    OrderNotFoundError,
    PositionLimitExceededError,
    RateLimitError,
    RiskError,
    StrategyError,
    TradingAgentError,
)


class TestBaseException:
    def test_message_stored(self) -> None:
        exc = TradingAgentError("something went wrong")
        assert exc.message == "something went wrong"

    def test_details_default_empty_dict(self) -> None:
        exc = TradingAgentError("msg")
        assert exc.details == {}

    def test_details_stored(self) -> None:
        exc = TradingAgentError("msg", details={"key": "value"})
        assert exc.details == {"key": "value"}

    def test_str_without_details(self) -> None:
        exc = TradingAgentError("plain message")
        assert str(exc) == "plain message"

    def test_str_includes_details(self) -> None:
        exc = TradingAgentError("error", details={"code": 404})
        result = str(exc)
        assert "error" in result
        assert "404" in result

    def test_is_exception(self) -> None:
        with pytest.raises(TradingAgentError):
            raise TradingAgentError("test")


class TestExceptionHierarchy:
    def test_missing_credentials_is_configuration(self) -> None:
        assert issubclass(MissingCredentialsError, ConfigurationError)

    def test_configuration_is_base(self) -> None:
        assert issubclass(ConfigurationError, TradingAgentError)

    def test_rate_limit_is_exchange(self) -> None:
        assert issubclass(RateLimitError, ExchangeError)

    def test_exchange_is_base(self) -> None:
        assert issubclass(ExchangeError, TradingAgentError)

    def test_connection_error_is_exchange(self) -> None:
        assert issubclass(ConnectionError, ExchangeError)

    def test_auth_error_is_exchange(self) -> None:
        assert issubclass(AuthenticationError, ExchangeError)

    def test_insufficient_funds_is_exchange(self) -> None:
        assert issubclass(InsufficientFundsError, ExchangeError)

    def test_order_not_found_is_exchange(self) -> None:
        assert issubclass(OrderNotFoundError, ExchangeError)

    def test_data_fetch_is_data(self) -> None:
        assert issubclass(DataFetchError, DataError)

    def test_data_validation_is_data(self) -> None:
        assert issubclass(DataValidationError, DataError)

    def test_data_is_base(self) -> None:
        assert issubclass(DataError, TradingAgentError)

    def test_invalid_signal_is_strategy(self) -> None:
        assert issubclass(InvalidSignalError, StrategyError)

    def test_strategy_is_base(self) -> None:
        assert issubclass(StrategyError, TradingAgentError)

    def test_max_drawdown_is_risk(self) -> None:
        assert issubclass(MaxDrawdownExceededError, RiskError)

    def test_position_limit_is_risk(self) -> None:
        assert issubclass(PositionLimitExceededError, RiskError)

    def test_risk_is_base(self) -> None:
        assert issubclass(RiskError, TradingAgentError)

    def test_insufficient_data_is_backtest(self) -> None:
        assert issubclass(InsufficientDataError, BacktestError)

    def test_backtest_is_base(self) -> None:
        assert issubclass(BacktestError, TradingAgentError)


class TestExceptionRaisable:
    @pytest.mark.parametrize("exc_class", [
        MissingCredentialsError,
        ConnectionError,
        AuthenticationError,
        RateLimitError,
        InsufficientFundsError,
        OrderNotFoundError,
        DataFetchError,
        DataValidationError,
        InvalidSignalError,
        MaxDrawdownExceededError,
        PositionLimitExceededError,
        InsufficientDataError,
    ])
    def test_all_exceptions_raisable(self, exc_class: type) -> None:
        with pytest.raises(TradingAgentError):
            raise exc_class("test message")
