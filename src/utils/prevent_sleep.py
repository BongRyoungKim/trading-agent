"""
Windows sleep prevention for long-running processes.

Calls SetThreadExecutionState to tell Windows the system is busy,
preventing standby/hibernate while the trading agent is active.
"""
from __future__ import annotations

import sys

from loguru import logger

# Windows execution state flags
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def enable() -> bool:
    """
    Prevent Windows from entering sleep/standby.
    Returns True if successful, False on non-Windows or failure.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
        )
        logger.info("Sleep prevention enabled (SetThreadExecutionState)")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to enable sleep prevention: {exc}")
        return False


def disable() -> None:
    """Restore default sleep behavior."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)  # type: ignore[attr-defined]
        logger.info("Sleep prevention disabled")
    except Exception:  # noqa: BLE001
        pass
