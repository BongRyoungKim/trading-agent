"""
Shared pytest fixtures for Trading Agent test suite.
"""
from __future__ import annotations

import os
from collections.abc import Generator
from unittest.mock import patch

import pytest

from src.config.settings import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    """
    Clear the lru_cache on get_settings() before and after each test.
    Prevents cached Settings from leaking state between test cases.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Patch environment variables to safe test defaults.
    Apply with: def test_foo(mock_env): ...
    """
    defaults = {
        "TRADING_MODE": "paper",
        "EXCHANGE": "binance",
        "BINANCE_API_KEY": "",
        "BINANCE_SECRET_KEY": "",
        "MAX_POSITION_RISK": "0.02",
        "MAX_DAILY_LOSS": "0.05",
        "MAX_OPEN_POSITIONS": "5",
        "MAX_DRAWDOWN_HALT": "0.15",
        "LOG_LEVEL": "DEBUG",
        "LOG_FILE_PATH": "logs/test.log",
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
    }
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)


@pytest.fixture()
def test_settings(mock_env: None) -> object:
    """Return a Settings instance with safe test defaults."""
    return get_settings()
