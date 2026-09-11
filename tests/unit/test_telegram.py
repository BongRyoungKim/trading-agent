"""Unit tests for Telegram notification client."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.utils.telegram import TelegramClient


@pytest.fixture()
def client() -> TelegramClient:
    return TelegramClient(bot_token="test_token", chat_id="12345")


@pytest.fixture()
def disabled_client() -> TelegramClient:
    return TelegramClient(bot_token="", chat_id="")


class TestTelegramClientEnabled:
    def test_is_enabled_with_credentials(self, client: TelegramClient) -> None:
        assert client.is_enabled is True

    def test_is_disabled_without_credentials(self, disabled_client: TelegramClient) -> None:
        assert disabled_client.is_enabled is False

    def test_send_returns_false_when_disabled(self, disabled_client: TelegramClient) -> None:
        result = disabled_client.send("hello")
        assert result is False


class TestTelegramClientSend:
    def test_send_success(self, client: TelegramClient) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = client.send("Test message")
        assert result is True

    def test_send_returns_false_on_network_error(self, client: TelegramClient) -> None:
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            result = client.send("Test message")
        assert result is False

    def test_send_never_raises(self, client: TelegramClient) -> None:
        with patch("urllib.request.urlopen", side_effect=Exception("unexpected")):
            # Should catch and return False, not propagate
            try:
                result = client.send("Test")
                assert result is False
            except Exception:
                pytest.fail("send() should never raise")


class TestTelegramFormatted:
    def _patched_send(self, client: TelegramClient, return_value: bool = True):
        return patch.object(client, "send", return_value=return_value)

    def test_send_order_filled_buy(self, client: TelegramClient) -> None:
        with self._patched_send(client) as mock_send:
            client.send_order_filled("BTC/USDT", "buy", 0.01, 50000.0)
            text = mock_send.call_args[0][0]
            assert "BTC/USDT" in text
            assert "BUY" in text
            assert "50,000" in text

    def test_send_order_filled_sell(self, client: TelegramClient) -> None:
        with self._patched_send(client) as mock_send:
            client.send_order_filled("BTC/USDT", "sell", 0.01, 48000.0)
            text = mock_send.call_args[0][0]
            assert "SELL" in text

    def test_send_position_closed_profit(self, client: TelegramClient) -> None:
        with self._patched_send(client) as mock_send:
            client.send_position_closed("BTC/USDT", 50000.0, 55000.0, 500.0, 10.0)
            text = mock_send.call_args[0][0]
            assert "500" in text
            assert "10.00" in text

    def test_send_risk_alert(self, client: TelegramClient) -> None:
        with self._patched_send(client) as mock_send:
            client.send_risk_alert("Drawdown limit reached")
            text = mock_send.call_args[0][0]
            assert "RISK ALERT" in text
            assert "Drawdown" in text

    def test_send_error(self, client: TelegramClient) -> None:
        with self._patched_send(client) as mock_send:
            client.send_error("Connection failed", context="get_ticker")
            text = mock_send.call_args[0][0]
            assert "ERROR" in text
            assert "Connection failed" in text

    def test_send_daily_report(self, client: TelegramClient) -> None:
        with self._patched_send(client) as mock_send:
            client.send_daily_report(
                date=datetime(2024, 1, 15, tzinfo=UTC),
                initial_capital=10000.0,
                final_capital=10300.0,
                realized_pnl=300.0,
                total_trades=5,
                win_rate=60.0,
            )
            text = mock_send.call_args[0][0]
            assert "2024-01-15" in text
            assert "300" in text
            assert "60.0%" in text

    def test_disabled_formatted_methods_return_false(self, disabled_client: TelegramClient) -> None:
        assert disabled_client.send_order_filled("BTC/USDT", "buy", 0.01, 50000.0) is False
        assert disabled_client.send_risk_alert("test") is False


class TestCriticalAlertsBypassHourlyBuffer:
    """send_risk_alert / send_error are 'critical' tier — always sent immediately,
    even when hourly_summary=True (they are ALSO queued so the hourly digest's
    event list stays complete)."""

    def _client(self) -> TelegramClient:
        return TelegramClient(bot_token="tok", chat_id="123", hourly_summary=True)

    def test_risk_alert_sent_immediately_in_hourly_mode(self) -> None:
        client = self._client()
        with patch.object(client, "send", return_value=True) as mock_send:
            result = client.send_risk_alert("Drawdown limit reached")
        mock_send.assert_called_once()
        assert result is True

    def test_risk_alert_also_queued_for_digest(self) -> None:
        client = self._client()
        with patch.object(client, "send", return_value=True):
            client.send_risk_alert("Drawdown limit reached")
        assert any("Drawdown" in e for e in client._buffer)  # noqa: SLF001

    def test_error_sent_immediately_in_hourly_mode(self) -> None:
        client = self._client()
        with patch.object(client, "send", return_value=True) as mock_send:
            result = client.send_error("Connection failed", context="get_ticker")
        mock_send.assert_called_once()
        assert result is True

    def test_error_also_queued_for_digest(self) -> None:
        client = self._client()
        with patch.object(client, "send", return_value=True):
            client.send_error("Connection failed", context="get_ticker")
        assert any("Connection failed" in e for e in client._buffer)  # noqa: SLF001


class TestNotifyLevelFiltering:
    """notify_level='critical' suppresses info-tier notifications entirely."""

    def test_info_method_suppressed_when_critical_only(self) -> None:
        client = TelegramClient(
            bot_token="tok", chat_id="123", notify_level="critical"
        )
        with patch.object(client, "send", return_value=True) as mock_send:
            result = client.send_order_filled("BTC/USDT", "buy", 0.01, 50000.0)
        mock_send.assert_not_called()
        assert result is True

    def test_critical_alert_not_suppressed_when_critical_only(self) -> None:
        client = TelegramClient(
            bot_token="tok", chat_id="123", notify_level="critical"
        )
        with patch.object(client, "send", return_value=True) as mock_send:
            client.send_risk_alert("Drawdown limit reached")
        mock_send.assert_called_once()

    def test_info_method_sent_when_level_all(self) -> None:
        client = TelegramClient(bot_token="tok", chat_id="123", notify_level="all")
        with patch.object(client, "send", return_value=True) as mock_send:
            client.send_order_filled("BTC/USDT", "buy", 0.01, 50000.0)
        mock_send.assert_called_once()


class TestQuietHoursFiltering:
    """quiet_hours suppresses info-tier notifications while active; critical
    alerts always go through regardless of quiet hours."""

    def _quiet_hours(self, is_quiet: bool):
        qh = MagicMock()
        qh.is_quiet.return_value = is_quiet
        return qh

    def test_info_method_suppressed_during_quiet_hours(self) -> None:
        client = TelegramClient(
            bot_token="tok", chat_id="123", quiet_hours=self._quiet_hours(True)
        )
        with patch.object(client, "send", return_value=True) as mock_send:
            result = client.send_order_filled("BTC/USDT", "buy", 0.01, 50000.0)
        mock_send.assert_not_called()
        assert result is True

    def test_info_method_sent_outside_quiet_hours(self) -> None:
        client = TelegramClient(
            bot_token="tok", chat_id="123", quiet_hours=self._quiet_hours(False)
        )
        with patch.object(client, "send", return_value=True) as mock_send:
            client.send_order_filled("BTC/USDT", "buy", 0.01, 50000.0)
        mock_send.assert_called_once()

    def test_critical_alert_not_suppressed_during_quiet_hours(self) -> None:
        client = TelegramClient(
            bot_token="tok", chat_id="123", quiet_hours=self._quiet_hours(True)
        )
        with patch.object(client, "send", return_value=True) as mock_send:
            client.send_risk_alert("Drawdown limit reached")
        mock_send.assert_called_once()


class TestFlushSummarySuppression:
    """When suppressed (critical-only or quiet hours), flush_summary must not
    send AND must not drop the buffered events — they carry over to the next
    (non-suppressed) flush."""

    def test_suppressed_flush_does_not_send(self) -> None:
        client = TelegramClient(
            bot_token="tok", chat_id="123", hourly_summary=True, notify_level="critical"
        )
        client._queue("some earlier event")  # noqa: SLF001
        with patch.object(client, "send", return_value=True) as mock_send:
            client.flush_summary()
        mock_send.assert_not_called()

    def test_suppressed_flush_preserves_buffer_for_next_flush(self) -> None:
        client = TelegramClient(
            bot_token="tok", chat_id="123", hourly_summary=True, notify_level="critical"
        )
        client._queue("order filled XYZ")  # noqa: SLF001
        with patch.object(client, "send", return_value=True):
            client.flush_summary()  # suppressed — should not clear buffer

        client._notify_level = "all"  # simulate suppression lifting  # noqa: SLF001
        with patch.object(client, "send", return_value=True) as mock_send:
            client.flush_summary()
        text = mock_send.call_args[0][0]
        assert "order filled XYZ" in text
