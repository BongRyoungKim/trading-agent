"""Unit tests for src/utils/prevent_sleep."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from src.utils import prevent_sleep


class TestEnable:
    def test_returns_false_on_non_windows(self) -> None:
        with patch.object(sys, "platform", "linux"):
            result = prevent_sleep.enable()
        assert result is False

    def test_returns_true_on_windows_success(self) -> None:
        mock_kernel = MagicMock()
        mock_ctypes = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel
        with patch.object(sys, "platform", "win32"), patch.dict("sys.modules", {"ctypes": mock_ctypes}):
            result = prevent_sleep.enable()
        assert result is True

    def test_returns_false_on_exception(self) -> None:
        mock_ctypes = MagicMock()
        mock_ctypes.windll.kernel32.SetThreadExecutionState.side_effect = OSError("fail")
        with patch.object(sys, "platform", "win32"), patch.dict("sys.modules", {"ctypes": mock_ctypes}):
            result = prevent_sleep.enable()
        assert result is False


class TestDisable:
    def test_returns_none_on_non_windows(self) -> None:
        with patch.object(sys, "platform", "linux"):
            result = prevent_sleep.disable()
        assert result is None

    def test_succeeds_silently_on_exception(self) -> None:
        mock_ctypes = MagicMock()
        mock_ctypes.windll.kernel32.SetThreadExecutionState.side_effect = OSError("fail")
        with patch.object(sys, "platform", "win32"), patch.dict("sys.modules", {"ctypes": mock_ctypes}):
            prevent_sleep.disable()  # should not raise
