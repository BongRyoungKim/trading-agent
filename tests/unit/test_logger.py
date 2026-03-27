"""Unit tests for src/utils/logger.py"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from loguru import logger as loguru_logger

from src.utils.logger import logger, setup_logger


class TestSetupLogger:
    def test_setup_logger_creates_log_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log_file = tmp_path / "sublogs" / "test.log"
        monkeypatch.setenv("TRADING_MODE", "paper")
        monkeypatch.setenv("EXCHANGE", "binance")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("LOG_FILE_PATH", str(log_file))

        from src.config.settings import get_settings
        get_settings.cache_clear()

        setup_logger()

        assert log_file.parent.exists()

    def test_setup_logger_can_be_called_twice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log_file = tmp_path / "logs" / "test.log"
        monkeypatch.setenv("TRADING_MODE", "paper")
        monkeypatch.setenv("EXCHANGE", "binance")
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        monkeypatch.setenv("LOG_FILE_PATH", str(log_file))

        from src.config.settings import get_settings
        get_settings.cache_clear()

        # Should not raise on repeated calls
        setup_logger()
        setup_logger()

    def test_logger_is_loguru_instance(self) -> None:
        # logger re-exported from utils.logger should be the loguru logger
        assert logger is loguru_logger
