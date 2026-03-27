"""Unit tests for StartupChecker."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.utils.startup_check import CheckResult, StartupChecker


# ── CheckResult ───────────────────────────────────────────────────────────────

class TestCheckResult:
    def test_passed(self):
        r = CheckResult("test", True, "ok")
        assert r.passed is True

    def test_failed(self):
        r = CheckResult("test", False, "bad")
        assert r.passed is False

    def test_critical_default(self):
        r = CheckResult("test", True, "ok")
        assert r.critical is True

    def test_non_critical(self):
        r = CheckResult("test", False, "warn", critical=False)
        assert r.critical is False

    def test_frozen(self):
        r = CheckResult("test", True, "ok")
        with pytest.raises(Exception):
            r.passed = False  # type: ignore[misc]


# ── check_credentials ─────────────────────────────────────────────────────────

class TestCheckCredentials:
    def _settings(self, exchange="binance", api_key="key", secret="secret",
                  access_key="ak", secret_key="sk"):
        s = MagicMock()
        s.exchange = exchange
        s.binance_api_key = api_key
        s.binance_secret_key = secret
        s.upbit_access_key = access_key
        s.upbit_secret_key = secret_key
        return s

    def test_binance_credentials_present(self):
        r = StartupChecker.check_credentials(self._settings(exchange="binance"))
        assert r.passed is True
        assert r.name == "credentials"

    def test_binance_missing_api_key(self):
        r = StartupChecker.check_credentials(self._settings(exchange="binance", api_key=""))
        assert r.passed is False
        assert "binance_api_key" in r.message

    def test_binance_missing_secret(self):
        r = StartupChecker.check_credentials(self._settings(exchange="binance", secret=""))
        assert r.passed is False

    def test_upbit_credentials_present(self):
        r = StartupChecker.check_credentials(self._settings(exchange="upbit"))
        assert r.passed is True

    def test_upbit_missing_access_key(self):
        r = StartupChecker.check_credentials(self._settings(exchange="upbit", access_key=""))
        assert r.passed is False
        assert "upbit_access_key" in r.message

    def test_exception_returns_failed(self):
        s = MagicMock()
        s.exchange = "binance"
        # Make attribute access raise
        type(s).binance_api_key = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        r = StartupChecker.check_credentials(s)
        assert r.passed is False


# ── check_exchange_connectivity ───────────────────────────────────────────────

class TestCheckExchangeConnectivity:
    def test_success(self):
        exchange = MagicMock()
        exchange.exchange_id = "binance"
        ticker = MagicMock()
        ticker.last = 50000.0
        exchange.get_ticker.return_value = ticker
        r = StartupChecker.check_exchange_connectivity(exchange, "BTC/USDT")
        assert r.passed is True
        assert "binance" in r.message
        assert "BTC/USDT" in r.message

    def test_failure_on_exception(self):
        exchange = MagicMock()
        exchange.exchange_id = "binance"
        exchange.get_ticker.side_effect = ConnectionError("timeout")
        r = StartupChecker.check_exchange_connectivity(exchange, "BTC/USDT")
        assert r.passed is False
        assert "timeout" in r.message

    def test_name_is_exchange_connectivity(self):
        exchange = MagicMock()
        exchange.exchange_id = "upbit"
        exchange.get_ticker.side_effect = RuntimeError("fail")
        r = StartupChecker.check_exchange_connectivity(exchange)
        assert r.name == "exchange_connectivity"


# ── check_risk_params ─────────────────────────────────────────────────────────

class TestCheckRiskParams:
    def _settings(self, risk=0.02, drawdown=0.15, daily_loss=0.05):
        s = MagicMock()
        s.max_position_risk = risk
        s.max_drawdown_halt = drawdown
        s.max_daily_loss = daily_loss
        return s

    def test_valid_params(self):
        r = StartupChecker.check_risk_params(self._settings())
        assert r.passed is True

    def test_position_risk_too_high(self):
        r = StartupChecker.check_risk_params(self._settings(risk=1.5))
        assert r.passed is False
        assert "max_position_risk" in r.message

    def test_position_risk_zero(self):
        r = StartupChecker.check_risk_params(self._settings(risk=0))
        assert r.passed is False

    def test_drawdown_too_high(self):
        r = StartupChecker.check_risk_params(self._settings(drawdown=2.0))
        assert r.passed is False
        assert "max_drawdown_halt" in r.message

    def test_daily_loss_zero(self):
        r = StartupChecker.check_risk_params(self._settings(daily_loss=0))
        assert r.passed is False

    def test_boundary_value_one(self):
        """Exactly 1.0 should be considered valid (100% risk = allowed but extreme)."""
        r = StartupChecker.check_risk_params(self._settings(risk=1.0, drawdown=1.0, daily_loss=1.0))
        assert r.passed is True

    def test_exception_returns_failed(self):
        s = MagicMock()
        type(s).max_position_risk = property(lambda self: (_ for _ in ()).throw(RuntimeError()))
        r = StartupChecker.check_risk_params(s)
        assert r.passed is False


# ── check_db_writable ─────────────────────────────────────────────────────────

class TestCheckDbWritable:
    def test_writable_dir(self, tmp_path):
        r = StartupChecker.check_db_writable(tmp_path)
        assert r.passed is True
        assert r.name == "db_writable"

    def test_nonexistent_dir_fails(self):
        r = StartupChecker.check_db_writable(Path("/nonexistent/path/xyz"))
        assert r.passed is False

    def test_default_path_is_cwd(self):
        # Default path (None) should use "." which is writable in test env
        r = StartupChecker.check_db_writable(None)
        assert r.passed is True


# ── check_telegram ────────────────────────────────────────────────────────────

class TestCheckTelegram:
    def _settings(self, token="tok", chat_id="123"):
        s = MagicMock()
        s.telegram_bot_token = token
        s.telegram_chat_id = chat_id
        return s

    def test_configured(self):
        r = StartupChecker.check_telegram(self._settings())
        assert r.passed is True
        assert r.critical is False

    def test_missing_token(self):
        r = StartupChecker.check_telegram(self._settings(token=""))
        assert r.passed is False
        assert r.critical is False  # still non-critical

    def test_missing_chat_id(self):
        r = StartupChecker.check_telegram(self._settings(chat_id=""))
        assert r.passed is False
        assert r.critical is False

    def test_exception_non_critical(self):
        s = MagicMock()
        type(s).telegram_bot_token = property(lambda self: (_ for _ in ()).throw(RuntimeError()))
        r = StartupChecker.check_telegram(s)
        assert r.passed is False
        assert r.critical is False


# ── run_all & all_critical_passed ─────────────────────────────────────────────

class TestRunAll:
    def _make_deps(self):
        settings = MagicMock()
        settings.exchange = "binance"
        settings.binance_api_key = "k"
        settings.binance_secret_key = "s"
        settings.max_position_risk = 0.02
        settings.max_drawdown_halt = 0.15
        settings.max_daily_loss = 0.05
        settings.telegram_bot_token = "tok"
        settings.telegram_chat_id = "123"

        exchange = MagicMock()
        exchange.exchange_id = "binance"
        ticker = MagicMock()
        ticker.last = 50000.0
        exchange.get_ticker.return_value = ticker
        return settings, exchange

    def test_run_all_returns_list(self, tmp_path):
        settings, exchange = self._make_deps()
        checker = StartupChecker()
        results = checker.run_all(settings, exchange, db_path=tmp_path)
        assert isinstance(results, list)
        assert len(results) == 5

    def test_all_passed(self, tmp_path):
        settings, exchange = self._make_deps()
        checker = StartupChecker()
        results = checker.run_all(settings, exchange, db_path=tmp_path)
        assert checker.all_critical_passed(results) is True

    def test_one_critical_failure_fails(self, tmp_path):
        settings, exchange = self._make_deps()
        settings.binance_api_key = ""  # missing key → credentials check fails
        checker = StartupChecker()
        results = checker.run_all(settings, exchange, db_path=tmp_path)
        assert checker.all_critical_passed(results) is False

    def test_non_critical_failure_does_not_fail(self, tmp_path):
        settings, exchange = self._make_deps()
        settings.telegram_bot_token = ""  # telegram → non-critical
        checker = StartupChecker()
        results = checker.run_all(settings, exchange, db_path=tmp_path)
        assert checker.all_critical_passed(results) is True

    def test_print_summary_returns_bool(self, tmp_path, capsys):
        settings, exchange = self._make_deps()
        checker = StartupChecker()
        results = checker.run_all(settings, exchange, db_path=tmp_path)
        ok = checker.print_summary(results)
        assert isinstance(ok, bool)


# ── main.py integration ───────────────────────────────────────────────────────

class TestMainIntegration:
    def test_skip_checks_flag_bypasses_validation(self):
        """--skip-checks should prevent StartupChecker from being called."""
        import sys
        from contextlib import ExitStack
        from unittest.mock import patch, MagicMock

        mock_engine = MagicMock()
        settings = MagicMock()
        settings.trading_mode = "paper"
        settings.exchange = "binance"
        settings.binance_api_key = "k"
        settings.binance_secret_key = "s"
        settings.upbit_access_key = "k"
        settings.upbit_secret_key = "s"
        settings.max_position_risk = 0.02
        settings.max_open_positions = 5
        settings.max_daily_loss = 0.05
        settings.max_drawdown_halt = 0.15

        with ExitStack() as stack:
            stack.enter_context(patch("src.main.get_settings", return_value=settings))
            stack.enter_context(patch("src.main.setup_logger"))
            stack.enter_context(patch("src.exchange.binance.BinanceClient"))
            stack.enter_context(patch("src.exchange.upbit.UpbitClient"))
            stack.enter_context(patch("src.portfolio.tracker.PortfolioTracker"))
            stack.enter_context(patch("src.portfolio.position_store.SQLitePositionStore"))
            stack.enter_context(patch("src.risk.manager.PortfolioState"))
            stack.enter_context(patch("src.risk.manager.RiskManager"))
            stack.enter_context(patch("src.strategy.registry.get_strategy"))
            stack.enter_context(patch("src.utils.telegram.get_telegram_client"))
            stack.enter_context(patch("src.portfolio.journal_store.SQLiteJournalStore"))
            stack.enter_context(patch("src.engine.TradingEngine", return_value=mock_engine))
            mock_checker_cls = stack.enter_context(
                patch("src.utils.startup_check.StartupChecker")
            )

            old_argv = sys.argv
            sys.argv = ["trading_agent", "--skip-checks"]
            try:
                from src.main import main
                main()
            finally:
                sys.argv = old_argv

        # StartupChecker should NOT have been instantiated
        mock_checker_cls.assert_not_called()
