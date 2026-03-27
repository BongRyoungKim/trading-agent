"""Unit tests for trailing stop loss."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.portfolio.models import Position
from src.portfolio.tracker import PortfolioTracker


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pos(
    symbol: str = "BTC/USDT",
    entry: float = 50000.0,
    stop: float | None = None,
    trailing_pct: float | None = None,
) -> Position:
    return Position(
        symbol=symbol,
        side="buy",
        amount=Decimal("0.1"),
        entry_price=Decimal(str(entry)),
        entry_time=datetime(2024, 1, 1, tzinfo=UTC),
        stop_loss=Decimal(str(stop)) if stop is not None else None,
        trailing_stop_pct=Decimal(str(trailing_pct)) if trailing_pct is not None else None,
    )


def _tracker(cash: float = 100_000.0) -> PortfolioTracker:
    return PortfolioTracker(initial_cash=Decimal(str(cash)))


# ── Position model ────────────────────────────────────────────────────────────

class TestPositionModel:
    def test_trailing_stop_pct_default_none(self):
        pos = _pos()
        assert pos.trailing_stop_pct is None

    def test_trailing_stop_pct_stored(self):
        pos = _pos(trailing_pct=2.0)
        assert pos.trailing_stop_pct == Decimal("2.0")

    def test_frozen(self):
        pos = _pos(trailing_pct=2.0)
        with pytest.raises(Exception):
            pos.trailing_stop_pct = Decimal("3.0")  # type: ignore[misc]


# ── PortfolioTracker.update_stop_loss ─────────────────────────────────────────

class TestUpdateStopLoss:
    def test_updates_stop_loss(self):
        tracker = _tracker()
        tracker.open_position(
            "BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"),
            stop_loss=Decimal("49000"),
        )
        updated = tracker.update_stop_loss("BTC/USDT", Decimal("50500"))
        assert updated.stop_loss == Decimal("50500")

    def test_returns_updated_position(self):
        tracker = _tracker()
        tracker.open_position("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"))
        result = tracker.update_stop_loss("BTC/USDT", Decimal("49000"))
        assert isinstance(result, Position)
        assert result.stop_loss == Decimal("49000")

    def test_get_position_reflects_update(self):
        tracker = _tracker()
        tracker.open_position("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"))
        tracker.update_stop_loss("BTC/USDT", Decimal("49000"))
        pos = tracker.get_position("BTC/USDT")
        assert pos.stop_loss == Decimal("49000")

    def test_preserves_other_fields(self):
        tracker = _tracker()
        tracker.open_position(
            "BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"),
            take_profit=Decimal("60000"),
            trailing_stop_pct=Decimal("2"),
        )
        updated = tracker.update_stop_loss("BTC/USDT", Decimal("49000"))
        assert updated.take_profit == Decimal("60000")
        assert updated.trailing_stop_pct == Decimal("2")
        assert updated.amount == Decimal("0.1")

    def test_update_missing_symbol_raises(self):
        from src.utils.exceptions import TradingAgentError
        tracker = _tracker()
        with pytest.raises(TradingAgentError):
            tracker.update_stop_loss("BTC/USDT", Decimal("49000"))

    def test_update_syncs_to_store(self):
        store = MagicMock()
        store.load_all.return_value = {}
        tracker = PortfolioTracker(initial_cash=Decimal("100000"), store=store)
        tracker.open_position("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"))
        tracker.update_stop_loss("BTC/USDT", Decimal("49500"))
        # save() called once for open, once for update
        assert store.save.call_count == 2
        last_saved: Position = store.save.call_args[0][0]
        assert last_saved.stop_loss == Decimal("49500")


# ── open_position with trailing_stop_pct ─────────────────────────────────────

class TestOpenPositionWithTrailing:
    def test_open_stores_trailing_pct(self):
        tracker = _tracker()
        pos = tracker.open_position(
            "BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"),
            trailing_stop_pct=Decimal("2"),
        )
        assert pos.trailing_stop_pct == Decimal("2")

    def test_open_without_trailing_pct_is_none(self):
        tracker = _tracker()
        pos = tracker.open_position("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"))
        assert pos.trailing_stop_pct is None


# ── Engine trailing stop ratchet ──────────────────────────────────────────────

class TestEngineTrailingStop:
    def _make_engine(self, trailing_pct: float | None = None):
        from decimal import Decimal
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

        portfolio = PortfolioTracker(initial_cash=Decimal("100000"))
        risk_state = PortfolioState(capital=Decimal("100000"), peak_capital=Decimal("100000"))
        risk_manager = RiskManager(settings, risk_state)

        engine = TradingEngine(
            settings=settings,
            exchange=MagicMock(),
            strategy=MagicMock(),
            risk_manager=risk_manager,
            portfolio=portfolio,
        )
        if trailing_pct is not None:
            engine.trailing_stop_pct = trailing_pct
        return engine, portfolio

    def test_trailing_stop_pct_property(self):
        engine, _ = self._make_engine()
        assert engine.trailing_stop_pct is None

    def test_trailing_stop_pct_setter(self):
        engine, _ = self._make_engine()
        engine.trailing_stop_pct = 2.0
        assert engine.trailing_stop_pct == Decimal("2.0")

    def test_trailing_stop_pct_setter_none(self):
        engine, _ = self._make_engine(trailing_pct=2.0)
        engine.trailing_stop_pct = None
        assert engine.trailing_stop_pct is None

    def test_trailing_ratchets_up_on_price_rise(self):
        """When price rises above entry, trailing stop should move up."""
        engine, portfolio = self._make_engine(trailing_pct=2.0)

        # Open a position manually
        portfolio.open_position(
            "BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"),
            stop_loss=Decimal("49000"),
        )

        # Mock: price rises to 55000
        ticker = MagicMock()
        ticker.last_price = Decimal("55000")
        ticker.volume = Decimal("1000")
        engine._exchange.get_ticker.return_value = ticker

        # Mock strategy: HOLD (no new signal)
        signal_mock = MagicMock()
        signal_mock.is_actionable.return_value = False
        engine._exchange.get_ohlcv_dataframe.return_value = MagicMock()
        engine._strategy_provider.generate_signal.return_value = signal_mock

        engine._process_symbol("BTC/USDT")

        # Trailing stop: 55000 * (1 - 2/100) = 53900
        pos = portfolio.get_position("BTC/USDT")
        assert pos.stop_loss == Decimal("55000") * (Decimal("1") - Decimal("2") / Decimal("100"))

    def test_trailing_does_not_ratchet_down(self):
        """When price drops, trailing stop should NOT move down."""
        engine, portfolio = self._make_engine(trailing_pct=2.0)

        portfolio.open_position(
            "BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"),
            stop_loss=Decimal("49000"),
        )

        # Price drops to 48000 — new trail would be 47040 < current 49000
        ticker = MagicMock()
        ticker.last_price = Decimal("48000")
        ticker.volume = Decimal("1000")
        engine._exchange.get_ticker.return_value = ticker

        signal_mock = MagicMock()
        signal_mock.is_actionable.return_value = False
        engine._exchange.get_ohlcv_dataframe.return_value = MagicMock()
        engine._strategy_provider.generate_signal.return_value = signal_mock

        # Price <= stop_loss (48000 <= 49000) → position should be closed
        # Check that the stop-loss close fires, NOT trailing ratchet
        engine._process_symbol("BTC/USDT")
        # Position should be closed by stop_loss trigger
        assert not portfolio.has_position("BTC/USDT")

    def test_trailing_stop_triggers_close_at_ratcheted_level(self):
        """After ratcheting up, a price drop should trigger the new stop."""
        engine, portfolio = self._make_engine(trailing_pct=2.0)

        portfolio.open_position(
            "BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"),
            stop_loss=Decimal("49000"),
        )

        # First tick: price rises to 55000 → stop ratchets to 53900
        ticker = MagicMock()
        ticker.last_price = Decimal("55000")
        ticker.volume = Decimal("1000")
        engine._exchange.get_ticker.return_value = ticker
        signal_mock = MagicMock()
        signal_mock.is_actionable.return_value = False
        engine._exchange.get_ohlcv_dataframe.return_value = MagicMock()
        engine._strategy_provider.generate_signal.return_value = signal_mock
        engine._process_symbol("BTC/USDT")

        pos = portfolio.get_position("BTC/USDT")
        assert pos is not None
        expected_stop = Decimal("55000") * Decimal("0.98")
        assert pos.stop_loss == expected_stop

        # Second tick: price drops to 53000 < 53900 → stop-loss triggered
        ticker.last_price = Decimal("53000")
        engine._process_symbol("BTC/USDT")
        assert not portfolio.has_position("BTC/USDT")

    def test_no_trailing_without_config(self):
        """Without trailing_stop_pct, stop_loss stays fixed."""
        engine, portfolio = self._make_engine()  # no trailing_pct

        portfolio.open_position(
            "BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"),
            stop_loss=Decimal("49000"),
        )

        ticker = MagicMock()
        ticker.last_price = Decimal("55000")
        ticker.volume = Decimal("1000")
        engine._exchange.get_ticker.return_value = ticker
        signal_mock = MagicMock()
        signal_mock.is_actionable.return_value = False
        engine._exchange.get_ohlcv_dataframe.return_value = MagicMock()
        engine._strategy_provider.generate_signal.return_value = signal_mock

        engine._process_symbol("BTC/USDT")
        pos = portfolio.get_position("BTC/USDT")
        assert pos.stop_loss == Decimal("49000")  # unchanged


# ── Position store: trailing_stop_pct persistence ────────────────────────────

class TestPositionStoreTrailing:
    def test_save_and_load_trailing_pct(self, tmp_path):
        from src.portfolio.position_store import SQLitePositionStore
        store = SQLitePositionStore(tmp_path / "pos.db")
        pos = Position(
            symbol="BTC/USDT",
            side="buy",
            amount=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=datetime(2024, 1, 1, tzinfo=UTC),
            stop_loss=Decimal("49000"),
            trailing_stop_pct=Decimal("2.0"),
        )
        store.save(pos)
        loaded = store.load_all()
        assert "BTC/USDT" in loaded
        assert loaded["BTC/USDT"].trailing_stop_pct == Decimal("2.0")

    def test_save_and_load_none_trailing_pct(self, tmp_path):
        from src.portfolio.position_store import SQLitePositionStore
        store = SQLitePositionStore(tmp_path / "pos.db")
        pos = Position(
            symbol="BTC/USDT",
            side="buy",
            amount=Decimal("0.1"),
            entry_price=Decimal("50000"),
            entry_time=datetime(2024, 1, 1, tzinfo=UTC),
        )
        store.save(pos)
        loaded = store.load_all()
        assert loaded["BTC/USDT"].trailing_stop_pct is None
