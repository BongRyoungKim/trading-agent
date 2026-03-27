"""Unit tests for src/data/cli.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.data.cli import cmd_download, cmd_list, main


# ── cmd_list ──────────────────────────────────────────────────────────────────

class TestCmdList:
    def test_empty_storage_prints_message(self, capsys):
        with patch("src.data.cli.OHLCVStorage") as MockStorage:
            MockStorage.return_value.available_series.return_value = []
            args = MagicMock()
            rc = cmd_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "No data stored yet" in out

    def test_lists_series(self, capsys):
        series = [
            {"exchange": "binance", "symbol": "BTC/USDT", "timeframe": "1h", "count": 500}
        ]
        with patch("src.data.cli.OHLCVStorage") as MockStorage:
            MockStorage.return_value.available_series.return_value = series
            rc = cmd_list(MagicMock())
        assert rc == 0
        out = capsys.readouterr().out
        assert "binance" in out
        assert "BTC/USDT" in out
        assert "500" in out

    def test_multiple_series_all_shown(self, capsys):
        series = [
            {"exchange": "binance", "symbol": "BTC/USDT", "timeframe": "1h", "count": 100},
            {"exchange": "upbit", "symbol": "ETH/KRW", "timeframe": "1d", "count": 200},
        ]
        with patch("src.data.cli.OHLCVStorage") as MockStorage:
            MockStorage.return_value.available_series.return_value = series
            rc = cmd_list(MagicMock())
        assert rc == 0
        out = capsys.readouterr().out
        assert "upbit" in out
        assert "ETH/KRW" in out


# ── cmd_download ──────────────────────────────────────────────────────────────

class TestCmdDownload:
    def _args(self, exchange="binance", symbol="BTC/USDT", timeframe="1h", days=30, batch_size=500):
        args = MagicMock()
        args.exchange = exchange
        args.symbol = symbol
        args.timeframe = timeframe
        args.days = days
        args.batch_size = batch_size
        return args

    def test_download_binance_returns_zero(self, capsys):
        with (
            patch("src.data.cli._build_client") as mock_build,
            patch("src.data.cli.OHLCVStorage"),
            patch("src.data.cli.DataCollector") as MockCollector,
        ):
            MockCollector.return_value.download_history.return_value = 100
            mock_build.return_value = MagicMock()
            rc = cmd_download(self._args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "100" in out

    def test_download_passes_args_to_collector(self):
        with (
            patch("src.data.cli._build_client"),
            patch("src.data.cli.OHLCVStorage"),
            patch("src.data.cli.DataCollector") as MockCollector,
        ):
            collector_instance = MockCollector.return_value
            collector_instance.download_history.return_value = 0
            cmd_download(self._args(symbol="ETH/USDT", timeframe="4h", days=7, batch_size=200))
            collector_instance.download_history.assert_called_once_with(
                symbol="ETH/USDT",
                timeframe="4h",
                days_back=7,
                batch_size=200,
            )


# ── main() ────────────────────────────────────────────────────────────────────

class TestMain:
    def test_no_command_exits(self):
        with pytest.raises(SystemExit):
            main([])

    def test_download_command_dispatches(self):
        with (
            patch("src.data.cli.setup_logger"),
            patch("src.data.cli.cmd_download", return_value=0) as mock_dl,
        ):
            rc = main(["download", "--symbol", "BTC/USDT"])
        assert rc == 0
        mock_dl.assert_called_once()

    def test_list_command_dispatches(self):
        with (
            patch("src.data.cli.setup_logger"),
            patch("src.data.cli.cmd_list", return_value=0) as mock_list,
        ):
            rc = main(["list"])
        assert rc == 0
        mock_list.assert_called_once()

    def test_download_requires_symbol(self):
        with pytest.raises(SystemExit):
            main(["download"])  # --symbol is required


# ── _build_client ─────────────────────────────────────────────────────────────

class TestBuildClient:
    def test_unsupported_exchange_raises(self):
        from src.data.cli import _build_client
        with (
            patch("src.data.cli.get_settings"),
            pytest.raises(ValueError, match="Unsupported exchange"),
        ):
            _build_client("kraken")

    def test_binance_client_built(self):
        from src.data.cli import _build_client
        mock_settings = MagicMock()
        mock_settings.binance_api_key = "k"
        mock_settings.binance_secret_key = "s"
        with (
            patch("src.data.cli.get_settings", return_value=mock_settings),
            patch("src.exchange.binance.BinanceClient") as MockBinance,
        ):
            MockBinance.return_value = MagicMock()
            client = _build_client("binance")
        assert client is not None

    def test_upbit_client_built(self):
        from src.data.cli import _build_client
        mock_settings = MagicMock()
        mock_settings.upbit_access_key = "k"
        mock_settings.upbit_secret_key = "s"
        with (
            patch("src.data.cli.get_settings", return_value=mock_settings),
            patch("src.exchange.upbit.UpbitClient") as MockUpbit,
        ):
            MockUpbit.return_value = MagicMock()
            client = _build_client("upbit")
        assert client is not None
