"""Unit tests for SymbolStrategyRouter."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.strategy.symbol_router import SymbolStrategyRouter


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_strat(name: str = "MockStrategy") -> MagicMock:
    s = MagicMock()
    type(s).__name__ = name
    s.min_required_bars.return_value = 1
    s.timeframe = "1h"
    return s


# ── Initialisation ────────────────────────────────────────────────────────────

class TestInit:
    def test_routes_only_valid(self):
        r = SymbolStrategyRouter(routes={"BTC/USDT": _mock_strat()})
        assert "BTC/USDT" in r.routes

    def test_default_only_valid(self):
        r = SymbolStrategyRouter(routes={}, default=_mock_strat())
        assert r.default is not None

    def test_routes_and_default_valid(self):
        r = SymbolStrategyRouter(
            routes={"BTC/USDT": _mock_strat()},
            default=_mock_strat("Fallback"),
        )
        assert len(r.routes) == 1
        assert r.default is not None

    def test_empty_routes_no_default_raises(self):
        with pytest.raises(ValueError, match="requires at least one route or a default"):
            SymbolStrategyRouter(routes={})

    def test_routes_copy_is_independent(self):
        """Mutating the original dict does not affect the router."""
        original = {"BTC/USDT": _mock_strat()}
        r = SymbolStrategyRouter(routes=original)
        original["ETH/USDT"] = _mock_strat()
        assert "ETH/USDT" not in r.routes


# ── __call__ (resolve) ────────────────────────────────────────────────────────

class TestResolve:
    def test_known_symbol_returns_mapped_strategy(self):
        strat = _mock_strat("BTC_Strategy")
        r = SymbolStrategyRouter(routes={"BTC/USDT": strat})
        assert r("BTC/USDT") is strat

    def test_unknown_symbol_returns_default(self):
        fallback = _mock_strat("Fallback")
        r = SymbolStrategyRouter(routes={"BTC/USDT": _mock_strat()}, default=fallback)
        assert r("ETH/USDT") is fallback

    def test_unknown_symbol_no_default_raises(self):
        r = SymbolStrategyRouter(routes={"BTC/USDT": _mock_strat()})
        with pytest.raises(KeyError, match="ETH/USDT"):
            r("ETH/USDT")

    def test_multiple_routes_each_resolved(self):
        btc = _mock_strat("BTC")
        eth = _mock_strat("ETH")
        r = SymbolStrategyRouter(routes={"BTC/USDT": btc, "ETH/USDT": eth})
        assert r("BTC/USDT") is btc
        assert r("ETH/USDT") is eth

    def test_default_strategy_used_for_many_unknowns(self):
        fallback = _mock_strat("Default")
        r = SymbolStrategyRouter(routes={}, default=fallback)
        assert r("BTC/USDT") is fallback
        assert r("SOL/USDT") is fallback
        assert r("ANYTHING") is fallback


# ── strategy_names ────────────────────────────────────────────────────────────

class TestStrategyNames:
    def test_names_contain_routes(self):
        s = _mock_strat("MAStrat")
        r = SymbolStrategyRouter(routes={"BTC/USDT": s})
        names = r.strategy_names()
        assert names["BTC/USDT"] == "MAStrat"

    def test_default_shown_as_wildcard(self):
        r = SymbolStrategyRouter(
            routes={"BTC/USDT": _mock_strat("BTC")},
            default=_mock_strat("DefaultStrat"),
        )
        names = r.strategy_names()
        assert names["*"] == "DefaultStrat"

    def test_no_default_no_wildcard_key(self):
        r = SymbolStrategyRouter(routes={"BTC/USDT": _mock_strat()})
        assert "*" not in r.strategy_names()

    def test_multiple_routes_all_present(self):
        r = SymbolStrategyRouter(
            routes={"BTC/USDT": _mock_strat("A"), "ETH/USDT": _mock_strat("B")}
        )
        names = r.strategy_names()
        assert "BTC/USDT" in names
        assert "ETH/USDT" in names


# ── Callable interface (engine compatibility) ─────────────────────────────────

class TestCallable:
    def test_is_callable(self):
        r = SymbolStrategyRouter(routes={"BTC/USDT": _mock_strat()})
        assert callable(r)

    def test_not_instance_of_base_strategy(self):
        """Engine uses `not isinstance(provider, BaseStrategy)` to detect callable."""
        from src.strategy.base import BaseStrategy
        r = SymbolStrategyRouter(routes={"BTC/USDT": _mock_strat()})
        assert not isinstance(r, BaseStrategy)


# ── Engine integration ────────────────────────────────────────────────────────

class TestEngineIntegration:
    def _make_engine(self, router):
        from decimal import Decimal
        from unittest.mock import MagicMock
        from src.engine import TradingEngine
        from src.portfolio.tracker import PortfolioTracker
        from src.risk.manager import PortfolioState, RiskManager

        settings = MagicMock()
        settings.trading_mode = "paper"
        settings.exchange = "binance"
        settings.max_position_risk = 0.02
        settings.max_open_positions = 5
        settings.max_drawdown_halt = 0.15
        settings.max_daily_loss = 0.05

        portfolio = PortfolioTracker(initial_cash=Decimal("10000"))
        risk_state = PortfolioState(capital=Decimal("10000"), peak_capital=Decimal("10000"))
        risk_manager = RiskManager(settings, risk_state)
        mock_exchange = MagicMock()

        engine = TradingEngine(
            settings=settings,
            exchange=mock_exchange,
            strategy=router,
            risk_manager=risk_manager,
            portfolio=portfolio,
        )
        return engine, mock_exchange

    def _make_df(self):
        from datetime import UTC, datetime, timedelta
        import pandas as pd
        base = datetime(2024, 1, 1, tzinfo=UTC)
        n = 20
        index = pd.DatetimeIndex([base + timedelta(hours=i) for i in range(n)])
        return pd.DataFrame(
            {"open": [50000.0]*n, "high": [50500.0]*n, "low": [49500.0]*n,
             "close": [50000.0]*n, "volume": [100.0]*n},
            index=index,
        )

    def test_router_resolves_correct_strategy(self):
        btc_strat = _mock_strat("BTC")
        eth_strat = _mock_strat("ETH")
        from src.strategy.models import Signal, SignalAction
        from datetime import UTC, datetime

        for s in (btc_strat, eth_strat):
            sig = MagicMock()
            sig.is_actionable.return_value = False
            s.generate_signal.return_value = sig

        router = SymbolStrategyRouter(
            routes={"BTC/USDT": btc_strat, "ETH/USDT": eth_strat}
        )
        engine, mock_exchange = self._make_engine(router)
        mock_exchange.get_ohlcv_dataframe.return_value = self._make_df()

        engine._process_symbol("BTC/USDT")
        engine._process_symbol("ETH/USDT")

        btc_strat.generate_signal.assert_called_once()
        eth_strat.generate_signal.assert_called_once()

    def test_router_fallback_used_for_unknown_symbol(self):
        fallback = _mock_strat("Fallback")
        sig = MagicMock()
        sig.is_actionable.return_value = False
        fallback.generate_signal.return_value = sig

        router = SymbolStrategyRouter(routes={}, default=fallback)
        engine, mock_exchange = self._make_engine(router)
        mock_exchange.get_ohlcv_dataframe.return_value = self._make_df()

        engine._process_symbol("SOL/USDT")
        fallback.generate_signal.assert_called_once()
