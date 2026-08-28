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

_BASE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)


def _format_record(record: dict) -> str:
    """
    loguru의 기본 포맷은 {message}만 렌더링해서, logger.warning("...", func=...,
    error=..., issues=...)처럼 넘긴 구조화 필드(extra)가 로그에 전혀 찍히지 않는
    문제가 있었다("Retryable error, backing off" / "Data quality issues — skipping
    tick" 등에서 실제 원인이 안 보였음). extra가 있으면 줄 끝에 key=value로 붙여
    보이게 한다. extra 값(예외 메시지 등)에 '{'/'}' 리터럴이 섞여 있으면 loguru가
    포맷 필드로 오인해 에러를 내므로 이스케이프 처리한다.
    """
    fmt = _BASE_FORMAT
    if record["extra"]:
        pairs = " ".join(f"{k}={v}" for k, v in record["extra"].items())
        pairs = pairs.replace("{", "{{").replace("}", "}}")
        fmt += f" | <dim>{pairs}</dim>"
    return fmt + "\n{exception}"


def setup_logger() -> None:
    """
    Configure loguru sinks: colorized stderr + rotating file.
    Must be called once before any logging occurs.
    """
    settings = get_settings()

    # Remove loguru's default handler to avoid duplicate output
    logger.remove()

    log_format = _format_record

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
