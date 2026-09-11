"""Unit tests for src/config/settings.py"""
import pytest
from pydantic import ValidationError

from src.config.settings import Settings, get_settings
from src.utils.exceptions import MissingCredentialsError


class TestDefaults:
    def test_default_trading_mode_is_paper(self, mock_env: None) -> None:
        settings = get_settings()
        assert settings.trading_mode == "paper"

    def test_default_exchange_is_binance(self, mock_env: None) -> None:
        settings = get_settings()
        assert settings.exchange == "binance"

    def test_default_risk_values(self, mock_env: None) -> None:
        settings = get_settings()
        assert settings.max_position_risk == 0.02
        assert settings.max_daily_loss == 0.05
        assert settings.max_open_positions == 5
        assert settings.max_drawdown_halt == 0.15


class TestValidation:
    def test_invalid_trading_mode_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADING_MODE", "invalid_mode")
        with pytest.raises(ValidationError):
            Settings()

    def test_invalid_exchange_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXCHANGE", "kraken")
        with pytest.raises(ValidationError):
            Settings()

    def test_risk_fraction_zero_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_POSITION_RISK", "0.0")
        with pytest.raises(ValidationError):
            Settings()

    def test_risk_fraction_above_one_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_POSITION_RISK", "1.5")
        with pytest.raises(ValidationError):
            Settings()

    def test_max_open_positions_zero_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_OPEN_POSITIONS", "0")
        with pytest.raises(ValidationError):
            Settings()


class TestLiveModeCredentialGate:
    def test_live_mode_without_api_key_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRADING_MODE", "live")
        monkeypatch.setenv("EXCHANGE", "binance")
        monkeypatch.setenv("BINANCE_API_KEY", "")
        monkeypatch.setenv("BINANCE_SECRET_KEY", "")
        with pytest.raises((MissingCredentialsError, ValidationError)):
            Settings()

    def test_live_mode_with_credentials_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRADING_MODE", "live")
        monkeypatch.setenv("EXCHANGE", "binance")
        monkeypatch.setenv("BINANCE_API_KEY", "test_key_abc123")
        monkeypatch.setenv("BINANCE_SECRET_KEY", "test_secret_xyz789")
        settings = Settings()
        assert settings.trading_mode == "live"

    def test_paper_mode_without_credentials_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRADING_MODE", "paper")
        monkeypatch.setenv("BINANCE_API_KEY", "")
        settings = Settings()
        assert settings.trading_mode == "paper"

    def test_upbit_live_mode_checks_upbit_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRADING_MODE", "live")
        monkeypatch.setenv("EXCHANGE", "upbit")
        monkeypatch.setenv("UPBIT_ACCESS_KEY", "")
        monkeypatch.setenv("UPBIT_SECRET_KEY", "")
        with pytest.raises((MissingCredentialsError, ValidationError)):
            Settings()


class TestNotificationSettings:
    def test_default_notify_level_is_all(self, mock_env: None) -> None:
        settings = get_settings()
        assert settings.telegram_notify_level == "all"

    def test_default_quiet_hours_is_empty(self, mock_env: None) -> None:
        settings = get_settings()
        assert settings.telegram_quiet_hours_utc == ""

    def test_notify_level_critical_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_NOTIFY_LEVEL", "critical")
        settings = Settings()
        assert settings.telegram_notify_level == "critical"

    def test_notify_level_invalid_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_NOTIFY_LEVEL", "verbose")
        with pytest.raises(ValidationError):
            Settings()

    def test_quiet_hours_custom_value_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_QUIET_HOURS_UTC", "22:00-07:00")
        settings = Settings()
        assert settings.telegram_quiet_hours_utc == "22:00-07:00"


class TestSingleton:
    def test_get_settings_returns_same_instance(self, mock_env: None) -> None:
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_cache_clear_allows_fresh_instance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRADING_MODE", "paper")
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        s1 = get_settings()

        get_settings.cache_clear()
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        s2 = get_settings()

        assert s1 is not s2
        assert s2.log_level == "DEBUG"
