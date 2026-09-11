"""
Centralized configuration module.
All other modules import settings from here - never read os.environ directly.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.utils.exceptions import MissingCredentialsError


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables and .env file.

    Usage:
        from src.config.settings import get_settings
        settings = get_settings()
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Trading Mode ─────────────────────────────────────────────────────────
    trading_mode: Literal["live", "paper", "backtest"] = "paper"
    exchange: Literal["binance", "upbit", "bybit"] = "upbit"

    # ── Exchange Credentials ──────────────────────────────────────────────────
    binance_api_key: str = ""
    binance_secret_key: str = ""
    upbit_access_key: str = ""
    upbit_secret_key: str = ""
    bybit_api_key: str = ""
    bybit_secret_key: str = ""

    # ── Risk Parameters ───────────────────────────────────────────────────────
    max_position_risk: float = 0.02
    max_daily_loss: float = 0.05
    max_open_positions: int = 5
    max_drawdown_halt: float = 0.15

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_file_path: str = "logs/trading_agent.log"

    # ── Notifications (optional) ──────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # "all" = every notification; "critical" = only risk alerts/errors (info suppressed)
    telegram_notify_level: Literal["all", "critical"] = "all"
    # "HH:MM-HH:MM" in UTC, wraparound supported (e.g. "22:00-07:00"). "" disables.
    telegram_quiet_hours_utc: str = ""

    # ── Market Hours (optional, disabled by default for 24/7 crypto) ─────────
    market_hours_enabled: bool = False
    trading_hours: str = "00:00-23:59"   # "HH:MM-HH:MM" in UTC
    trading_days: str = "mon-sun"        # "mon-fri" or "mon,wed,fri"

    # ── Web Dashboard (optional) ──────────────────────────────────────────────
    dashboard_port: int = 0              # 0 = disabled

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("max_position_risk", "max_daily_loss", "max_drawdown_halt")
    @classmethod
    def validate_risk_fraction(cls, v: float, info: object) -> float:
        if not 0.0 < v <= 1.0:
            raise ValueError(f"{info.field_name} must be between 0.0 and 1.0, got {v}")
        return v

    @field_validator("max_open_positions")
    @classmethod
    def validate_positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"max_open_positions must be positive, got {v}")
        return v

    @model_validator(mode="after")
    def validate_live_credentials(self) -> "Settings":
        """
        HARD GATE: Live trading mode requires exchange credentials.
        Raises at startup - never at trade time.
        """
        if self.trading_mode != "live":
            return self

        credential_map = {
            "binance": ("binance_api_key", "binance_secret_key"),
            "upbit": ("upbit_access_key", "upbit_secret_key"),
            "bybit": ("bybit_api_key", "bybit_secret_key"),
        }
        key_field, secret_field = credential_map[self.exchange]
        missing = []
        if not getattr(self, key_field):
            missing.append(key_field.upper())
        if not getattr(self, secret_field):
            missing.append(secret_field.upper())

        if missing:
            raise MissingCredentialsError(
                f"Live trading mode requires credentials for {self.exchange}",
                details={"missing": ", ".join(missing), "exchange": self.exchange},
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the singleton Settings instance.
    Parsed once per process; subsequent calls return the cached object.
    Use get_settings.cache_clear() in tests to reset between test cases.
    """
    return Settings()
