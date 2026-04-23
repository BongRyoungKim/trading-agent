"""Unit tests for src/main.py."""
from __future__ import annotations

import sys
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest


def _mock_settings(mode: str = "paper", exchange: str = "binance") -> MagicMock:
    s = MagicMock()
    s.trading_mode = mode
    s.exchange = exchange
    s.binance_api_key = "k"
    s.binance_secret_key = "s"
    s.upbit_access_key = "k"
    s.upbit_secret_key = "s"
    s.max_position_risk = 0.02
    s.max_open_positions = 5
    s.max_daily_loss = 0.05
    s.max_drawdown_halt = 0.15
    return s


def _run_main(argv: list[str], settings: MagicMock | None = None):
    """
    Run src.main.main() with heavy deps patched.
    Returns (engine_mock, patches_dict).
    """
    if settings is None:
        settings = _mock_settings()

    mock_engine = MagicMock()
    mock_strategy = MagicMock()

    with ExitStack() as stack:
        p_settings = stack.enter_context(patch("src.main.get_settings", return_value=settings))
        p_logger = stack.enter_context(patch("src.main.setup_logger"))
        p_binance = stack.enter_context(patch("src.exchange.binance.BinanceClient"))
        p_upbit = stack.enter_context(patch("src.exchange.upbit.UpbitClient"))
        # PortfolioTracker.from_store is called now
        p_tracker_cls = stack.enter_context(patch("src.portfolio.tracker.PortfolioTracker"))
        p_pos_store = stack.enter_context(patch("src.portfolio.position_store.SQLitePositionStore"))
        p_pstate = stack.enter_context(patch("src.risk.manager.PortfolioState"))
        p_risk = stack.enter_context(patch("src.risk.manager.RiskManager"))
        # get_strategy is used instead of direct class import
        p_get_strategy = stack.enter_context(
            patch("src.strategy.registry.get_strategy", return_value=mock_strategy)
        )
        p_telegram = stack.enter_context(patch("src.utils.telegram.get_telegram_client"))
        p_jstore = stack.enter_context(patch("src.portfolio.journal_store.SQLiteJournalStore"))
        p_engine_cls = stack.enter_context(patch("src.engine.TradingEngine", return_value=mock_engine))

        old_argv = sys.argv
        sys.argv = ["trading_agent"] + argv
        try:
            from src.main import main
            main()
        finally:
            sys.argv = old_argv

    patches = {
        "get_settings": p_settings,
        "BinanceClient": p_binance,
        "UpbitClient": p_upbit,
        "get_strategy": p_get_strategy,
        "TradingEngine": p_engine_cls,
    }
    return mock_engine, patches


class TestMain:
    def test_paper_mode_starts_engine(self):
        engine, _ = _run_main([])
        engine.start.assert_called_once()

    def test_default_symbol_is_btc_krw(self):
        engine, _ = _run_main([])
        call_args = engine.start.call_args
        symbols = call_args[1].get("symbols") or call_args[0][0]
        assert "BTC/KRW" in symbols

    def test_custom_symbols(self):
        engine, _ = _run_main(["--symbols", "ETH/USDT", "BNB/USDT"])
        call_args = engine.start.call_args
        symbols = call_args[1].get("symbols") or call_args[0][0]
        assert "ETH/USDT" in symbols
        assert "BNB/USDT" in symbols

    def test_strategy_uses_first_symbol(self):
        _, patches = _run_main(["--symbols", "ETH/USDT"])
        get_strategy = patches["get_strategy"]
        call_kwargs = get_strategy.call_args[1]
        assert call_kwargs.get("symbol") == "ETH/USDT"

    def test_default_strategy_is_ma_crossover(self):
        _, patches = _run_main([])
        get_strategy = patches["get_strategy"]
        strategy_name = get_strategy.call_args[0][0]
        assert strategy_name == "MACrossoverStrategy"

    def test_custom_strategy_name(self):
        _, patches = _run_main(["--strategy", "RSIStrategy"])
        get_strategy = patches["get_strategy"]
        strategy_name = get_strategy.call_args[0][0]
        assert strategy_name == "RSIStrategy"

    def test_strategy_params_forwarded(self):
        _, patches = _run_main(
            ["--strategy", "MACrossoverStrategy",
             "--strategy-params", '{"fast_period": 10}']
        )
        get_strategy = patches["get_strategy"]
        call_kwargs = get_strategy.call_args[1]
        assert call_kwargs.get("fast_period") == 10

    def test_invalid_strategy_params_json_exits(self):
        import pytest
        with pytest.raises(SystemExit):
            _run_main(["--strategy-params", "{not valid json}"])

    def test_interval_passed_to_start(self):
        engine, _ = _run_main(["--interval", "120"])
        call_args = engine.start.call_args
        interval = call_args[1].get("interval_seconds") or call_args[0][1]
        assert interval == 120

    def test_upbit_exchange_builds_upbit_client(self):
        _, patches = _run_main([], settings=_mock_settings(exchange="upbit"))
        patches["UpbitClient"].assert_called_once()

    def test_binance_exchange_builds_binance_client(self):
        _, patches = _run_main([], settings=_mock_settings(exchange="binance"))
        patches["BinanceClient"].assert_called_once()

    def test_engine_created_with_correct_settings(self):
        settings = _mock_settings()
        _, patches = _run_main([], settings=settings)
        engine_cls = patches["TradingEngine"]
        call_kwargs = engine_cls.call_args[1]
        assert call_kwargs["settings"] is settings

    def test_mode_override_paper(self):
        engine, _ = _run_main(["--mode", "paper"])
        engine.start.assert_called_once()

    def test_list_strategies_prints_and_exits(self, capsys):
        old_argv = sys.argv
        sys.argv = ["trading_agent", "--list-strategies"]
        try:
            from src.main import main
            with patch("src.main.setup_logger"):
                main()
        finally:
            sys.argv = old_argv
        out = capsys.readouterr().out
        assert "MACrossoverStrategy" in out


# ── Per-symbol strategy assignment ───────────────────────────────────────────

class TestSymbolStrategyFlag:
    def test_symbol_strategy_invalid_format_exits(self):
        with pytest.raises(SystemExit):
            _run_main(["--symbol-strategy", "BTC_USDT_no_equals"])

    def test_symbol_strategy_builds_router(self):
        """When --symbol-strategy is given, a SymbolStrategyRouter is passed to TradingEngine."""
        from src.strategy.symbol_router import SymbolStrategyRouter

        _, patches = _run_main([
            "--symbols", "BTC/USDT", "ETH/USDT",
            "--symbol-strategy", "BTC/USDT=MACrossoverStrategy",
            "--symbol-strategy", "ETH/USDT=RSIStrategy",
        ])
        engine_cls = patches["TradingEngine"]
        strategy_arg = engine_cls.call_args[1]["strategy"]
        assert isinstance(strategy_arg, SymbolStrategyRouter)

    def test_symbol_strategy_partial_uses_default_fallback(self):
        """Only BTC/USDT has explicit strategy — ETH/USDT gets --strategy default."""
        from src.strategy.symbol_router import SymbolStrategyRouter

        _, patches = _run_main([
            "--symbols", "BTC/USDT", "ETH/USDT",
            "--symbol-strategy", "BTC/USDT=MACrossoverStrategy",
        ])
        engine_cls = patches["TradingEngine"]
        strategy_arg = engine_cls.call_args[1]["strategy"]
        assert isinstance(strategy_arg, SymbolStrategyRouter)
        assert strategy_arg.default is not None

    def test_no_symbol_strategy_uses_single_strategy(self):
        """Without --symbol-strategy, the old single-strategy path is taken."""
        from src.strategy.symbol_router import SymbolStrategyRouter

        _, patches = _run_main(["--symbols", "BTC/USDT"])
        engine_cls = patches["TradingEngine"]
        strategy_arg = engine_cls.call_args[1]["strategy"]
        assert not isinstance(strategy_arg, SymbolStrategyRouter)

    def test_symbol_strategy_calls_get_strategy_for_each(self):
        """get_strategy is called once per --symbol-strategy entry."""
        _, patches = _run_main([
            "--symbols", "BTC/USDT", "ETH/USDT",
            "--symbol-strategy", "BTC/USDT=MACrossoverStrategy",
            "--symbol-strategy", "ETH/USDT=RSIStrategy",
        ])
        get_strategy = patches["get_strategy"]
        # Called for each explicit mapping (BTC + ETH)
        assert get_strategy.call_count == 2
