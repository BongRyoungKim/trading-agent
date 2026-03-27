"""
Structured logging setup using loguru.
Call setup_logger() once at application startup.
Other modules: from src.utils.logger import logger
"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from src.config.settings import get_settings


def setup_logger() -> None:
    """
    Configure loguru sinks: colorized stderr + rotating file.
    Must be called once before any logging occurs.
    """
    settings = get_settings()

    # Remove loguru's default handler to avoid duplicate output
    logger.remove()

    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    # Console sink (stderr, colorized)
    logger.add(
        sys.stderr,
        format=log_format,
        level=settings.log_level,
        colorize=True,
        backtrace=True,
        diagnose=True,
    )

    # File sink with rotation
    log_path = Path(settings.log_file_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger.add(
        str(log_path),
        format=log_format,
        level=settings.log_level,
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        backtrace=True,
        diagnose=False,  # Disable in file to avoid sensitive data leaks
        enqueue=True,    # Thread-safe async logging
    )

    logger.info(
        "Logger initialized",
        mode=settings.trading_mode,
        exchange=settings.exchange,
        log_level=settings.log_level,
    )


__all__ = ["logger", "setup_logger"]
