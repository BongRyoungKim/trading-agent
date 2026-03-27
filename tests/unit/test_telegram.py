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
