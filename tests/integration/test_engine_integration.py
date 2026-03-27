"""
Integration tests: full trading loop using in-memory SQLite and mocked exchange.
Verifies that TradingEngine, RiskManager, PortfolioTracker, and Strategy
work together correctly end-to-end without calling real exchange APIs.
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.config.settings import Settings
from src.engine import TradingEngine
from src.portfolio.tracker import PortfolioTracker
from src.risk.manager import PortfolioState, RiskManager
from src.strategy.ma_crossover import MACrossoverStrategy
from src.utils.telegram import TelegramClient


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_settings(**overrides) -> Settings:
    """Build a Settings instance suitable for paper trading tests."""
    env = {
        "TRADING_MODE": "paper",
        "EXCHANGE": "binance",
        "MAX_POSITION_RISK": "0.02",
        "MAX_DAILY_LOSS": "0.05",
        "MAX_OPEN_POSITIONS": "5",
        "MAX_DRAWDOWN_HALT": "0.15",
        "LOG_LEVEL": "DEBUG",
    }
    env.update({k.upper(): str(v) for k, v in overrides.items()})
    with patch.dict("os.environ", env, clear=False):
        s = Settings()
    return s


def _make_ohlcv(prices: list[float], timeframe_minutes: int = 60) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a price list."""
    base = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    for i, p in enumerate(prices):
        rows.append({
            "timestamp": base + timedelta(minutes=i * timeframe_minutes),
            "open": p,
            "high": p * 1.01,
            "low": p * 0.99,
            "close": p,
            "volume": 100.0,
        })
    df = pd.DataFrame(rows).set_index("timestamp")
    return df


def _make_buy_df() -> pd.DataFrame:
    """OHLCV that produces a BUY from MACrossover (fast crosses above slow)."""
    # 30 bars at 49000, then 1 bar at 60000 → fast SMA(10) crosses above slow SMA(20)
    return _make_ohlcv([49000.0] * 30 + [60000.0])


def _make_sell_df() -> pd.DataFrame:
    """OHLCV that produces a SELL from MACrossover (fast crosses below slow)."""
    # 30 bars at 60000, then 1 bar at 40000 → fast SMA(10) crosses below slow SMA(20)
    return _make_ohlcv([60000.0] * 30 + [40000.0])


def _make_ticker(price: float) -> MagicMock:
    t = MagicMock()
    t.last_price = Decimal(str(price))
    return t


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def settings():
    return _make_settings()


@pytest.fixture()
def portfolio():
    return PortfolioTracker(initial_cash=Decimal("10000"))


@pytest.fixture()
def risk_manager(settings):
    state = PortfolioState(
        capital=Decimal("10000"),
        peak_capital=Decimal("10000"),
    )
    return RiskManager(settings, state)


@pytest.fixture()
def strategy():
    return MACrossoverStrategy(symbol="BTC/USDT", fast_period=10, slow_period=20)


@pytest.fixture()
def telegram():
    return TelegramClient("", "")  # disabled


@pytest.fixture()
def exchange():
    ex = MagicMock()
    ex.get_ticker.return_value = _make_ticker(50000.0)
    ex.get_ohlcv_dataframe.return_value = _make_ohlcv([50000.0] * 50)
    return ex


@pytest.fixture()
def engine(settings, exchange, strategy, risk_manager, portfolio, telegram):
    return TradingEngine(settings, exchange, strategy, risk_manager, portfolio, telegram)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestFullTradingLoop:
    def test_buy_signal_opens_position(self, engine, exchange, portfolio):
        """BUY signal → position is opened in portfolio."""
        exchange.get_ohlcv_dataframe.return_value = _make_buy_df()
        exchange.get_ticker.return_value = _make_ticker(60000.0)

        engine.tick("BTC/USDT")

        assert portfolio.has_position("BTC/USDT")

    def test_sell_signal_closes_position(self, engine, exchange, portfolio):
        """Open a position manually, then SELL signal closes it."""
        exchange.get_ohlcv_dataframe.return_value = _make_buy_df()
        exchange.get_ticker.return_value = _make_ticker(60000.0)
        engine.tick("BTC/USDT")
        assert portfolio.has_position("BTC/USDT")

        exchange.get_ohlcv_dataframe.return_value = _make_sell_df()
        exchange.get_ticker.return_value = _make_ticker(50000.0)
        engine.tick("BTC/USDT")

        assert not portfolio.has_position("BTC/USDT")

    def test_hold_does_not_change_portfolio(self, engine, exchange, portfolio):
        """HOLD signal leaves portfolio unchanged."""
        # Flat prices → no crossover → HOLD
        exchange.get_ohlcv_dataframe.return_value = _make_ohlcv([50000.0] * 50)
        engine.tick("BTC/USDT")
        assert not portfolio.has_position("BTC/USDT")

    def test_realized_pnl_after_round_trip(self, engine, exchange, portfolio):
        """Full round-trip trade records non-zero realized PnL."""
        exchange.get_ohlcv_dataframe.return_value = _make_buy_df()
        exchange.get_ticker.return_value = _make_ticker(60000.0)
        engine.tick("BTC/USDT")

        exchange.get_ohlcv_dataframe.return_value = _make_sell_df()
        exchange.get_ticker.return_value = _make_ticker(65000.0)  # profitable close
        engine.tick("BTC/USDT")

        assert portfolio.realized_pnl != Decimal("0")

    def test_drawdown_halt_blocks_new_position(
        self, settings, exchange, strategy, telegram, portfolio
    ):
        """If drawdown exceeds halt threshold, new positions are blocked."""
        # Set capital way below peak to trigger drawdown halt
        state = PortfolioState(
            capital=Decimal("5000"),   # 50% drawdown
            peak_capital=Decimal("10000"),
        )
        rm = RiskManager(settings, state)
        eng = TradingEngine(settings, exchange, strategy, rm, portfolio, telegram)

        exchange.get_ohlcv_dataframe.return_value = _make_buy_df()
        exchange.get_ticker.return_value = _make_ticker(60000.0)
        eng.tick("BTC/USDT")

        assert not portfolio.has_position("BTC/USDT")

    def test_position_limit_blocks_excess(self, settings, exchange, strategy, telegram):
        """Position limit (max_open_positions=1) prevents opening a second trade."""
        overrides_settings = _make_settings(max_open_positions=1)
        port = PortfolioTracker(initial_cash=Decimal("20000"))
        state = PortfolioState(
            capital=Decimal("20000"),
            peak_capital=Decimal("20000"),
        )
        rm = RiskManager(overrides_settings, state)
        eng = TradingEngine(overrides_settings, exchange, strategy, rm, port, telegram)

        exchange.get_ohlcv_dataframe.return_value = _make_buy_df()
        exchange.get_ticker.return_value = _make_ticker(60000.0)

        # First tick: opens position, increments open_positions counter
        eng.tick("BTC/USDT")
        assert port.has_position("BTC/USDT")

        # Simulate a second symbol tick — position limit should block it
        eng.tick("ETH/USDT")
        assert not port.has_position("ETH/USDT")

    def test_tick_error_does_not_crash_engine(self, engine, exchange):
        """Exchange error inside tick is caught; engine remains operational."""
        exchange.get_ohlcv_dataframe.side_effect = RuntimeError("network error")
        engine.tick("BTC/USDT")  # must not raise

    def test_multiple_ticks_idempotent_when_no_signal(self, engine, exchange, portfolio):
        """Multiple ticks without a signal change don't modify portfolio."""
        exchange.get_ohlcv_dataframe.return_value = _make_ohlcv([50000.0] * 50)
        for _ in range(5):
            engine.tick("BTC/USDT")
        assert not portfolio.has_position("BTC/USDT")
        assert portfolio.realized_pnl == Decimal("0")


class TestEngineStopLifecycle:
    def test_engine_stop_unblocks_start(self, engine):
        """Engine.start() blocks until stop() is called."""
        mock_scheduler = MagicMock()
        mock_scheduler.running = False
        engine._scheduler = mock_scheduler

        with patch.object(engine, "_register_signal_handlers"):
            started = threading.Event()

            def _run():
                started.set()
                engine.start(["BTC/USDT"], interval_seconds=3600)

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            started.wait(timeout=1)
            engine.stop()
            t.join(timeout=2)

        assert not t.is_alive()
