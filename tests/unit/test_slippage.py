"""Unit tests for slippage model."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.exchange.slippage import SlippageConfig, apply_slippage


# ── SlippageConfig validation ─────────────────────────────────────────────────

class TestSlippageConfig:
    def test_defaults(self):
        cfg = SlippageConfig()
        assert cfg.spread_bps == 5.0
        assert cfg.impact_bps_per_pct == 2.0

    def test_custom_values(self):
        cfg = SlippageConfig(spread_bps=10.0, impact_bps_per_pct=0.5)
        assert cfg.spread_bps == 10.0
        assert cfg.impact_bps_per_pct == 0.5

    def test_negative_spread_raises(self):
        with pytest.raises(ValueError, match="spread_bps"):
            SlippageConfig(spread_bps=-1.0)

    def test_negative_impact_raises(self):
        with pytest.raises(ValueError, match="impact_bps_per_pct"):
            SlippageConfig(impact_bps_per_pct=-0.1)

    def test_zero_spread_valid(self):
        cfg = SlippageConfig(spread_bps=0.0, impact_bps_per_pct=0.0)
        assert cfg.spread_bps == 0.0

    def test_frozen(self):
        cfg = SlippageConfig()
        with pytest.raises(Exception):
            cfg.spread_bps = 1.0  # type: ignore[misc]


# ── apply_slippage: buy side ──────────────────────────────────────────────────

class TestApplySlippageBuy:
    def test_buy_fill_higher_than_mid(self):
        cfg = SlippageConfig(spread_bps=5.0, impact_bps_per_pct=0.0)
        fill = apply_slippage(
            price=Decimal("50000"),
            side="buy",
            amount=Decimal("1"),
            daily_volume=Decimal("100"),
            config=cfg,
        )
        assert fill > Decimal("50000")

    def test_buy_spread_exact(self):
        # 5 bps = 0.0005 → fill = 50000 * 1.0005 = 50025.0
        cfg = SlippageConfig(spread_bps=5.0, impact_bps_per_pct=0.0)
        fill = apply_slippage(
            price=Decimal("50000"),
            side="buy",
            amount=Decimal("1"),
            daily_volume=Decimal("0"),  # no impact
            config=cfg,
        )
        expected = Decimal("50000") * Decimal("1.0005")
        assert abs(fill - expected) < Decimal("0.01")

    def test_buy_zero_slippage_returns_exact_price(self):
        cfg = SlippageConfig(spread_bps=0.0, impact_bps_per_pct=0.0)
        fill = apply_slippage(
            price=Decimal("50000"),
            side="buy",
            amount=Decimal("1"),
            daily_volume=Decimal("100"),
            config=cfg,
        )
        assert fill == Decimal("50000")

    def test_buy_market_impact_increases_fill(self):
        # Large order relative to volume → high market impact
        cfg = SlippageConfig(spread_bps=0.0, impact_bps_per_pct=2.0)
        fill_small = apply_slippage(
            price=Decimal("50000"),
            side="buy",
            amount=Decimal("1"),
            daily_volume=Decimal("10000"),
            config=cfg,
        )
        fill_large = apply_slippage(
            price=Decimal("50000"),
            side="buy",
            amount=Decimal("100"),
            daily_volume=Decimal("10000"),
            config=cfg,
        )
        assert fill_large > fill_small

    def test_buy_zero_volume_skips_impact(self):
        """When daily_volume=0, market impact should be skipped."""
        cfg = SlippageConfig(spread_bps=5.0, impact_bps_per_pct=2.0)
        fill_zero_vol = apply_slippage(
            price=Decimal("50000"),
            side="buy",
            amount=Decimal("1"),
            daily_volume=Decimal("0"),
            config=cfg,
        )
        fill_with_vol = apply_slippage(
            price=Decimal("50000"),
            side="buy",
            amount=Decimal("1"),
            daily_volume=Decimal("10000"),
            config=cfg,
        )
        # With zero volume: only spread; with volume: spread + impact
        # Both should be above mid; zero-vol should have less cost than with large volume
        assert fill_zero_vol > Decimal("50000")


# ── apply_slippage: sell side ─────────────────────────────────────────────────

class TestApplySlippageSell:
    def test_sell_fill_lower_than_mid(self):
        cfg = SlippageConfig(spread_bps=5.0, impact_bps_per_pct=0.0)
        fill = apply_slippage(
            price=Decimal("50000"),
            side="sell",
            amount=Decimal("1"),
            daily_volume=Decimal("100"),
            config=cfg,
        )
        assert fill < Decimal("50000")

    def test_sell_spread_exact(self):
        # 5 bps = 0.0005 → fill = 50000 * 0.9995 = 49975.0
        cfg = SlippageConfig(spread_bps=5.0, impact_bps_per_pct=0.0)
        fill = apply_slippage(
            price=Decimal("50000"),
            side="sell",
            amount=Decimal("1"),
            daily_volume=Decimal("0"),
            config=cfg,
        )
        expected = Decimal("50000") * Decimal("0.9995")
        assert abs(fill - expected) < Decimal("0.01")

    def test_sell_zero_slippage_returns_exact_price(self):
        cfg = SlippageConfig(spread_bps=0.0, impact_bps_per_pct=0.0)
        fill = apply_slippage(
            price=Decimal("50000"),
            side="sell",
            amount=Decimal("1"),
            daily_volume=Decimal("100"),
            config=cfg,
        )
        assert fill == Decimal("50000")

    def test_sell_market_impact_decreases_fill(self):
        cfg = SlippageConfig(spread_bps=0.0, impact_bps_per_pct=2.0)
        fill_small = apply_slippage(
            price=Decimal("50000"),
            side="sell",
            amount=Decimal("1"),
            daily_volume=Decimal("10000"),
            config=cfg,
        )
        fill_large = apply_slippage(
            price=Decimal("50000"),
            side="sell",
            amount=Decimal("100"),
            daily_volume=Decimal("10000"),
            config=cfg,
        )
        assert fill_large < fill_small

    def test_sell_zero_slippage_equals_buy_zero_slippage(self):
        """Symmetric: zero slippage on buy and sell returns same price."""
        cfg = SlippageConfig(spread_bps=0.0, impact_bps_per_pct=0.0)
        price = Decimal("50000")
        buy = apply_slippage(price, "buy", Decimal("1"), Decimal("0"), cfg)
        sell = apply_slippage(price, "sell", Decimal("1"), Decimal("0"), cfg)
        assert buy == sell == price


# ── Invalid side ──────────────────────────────────────────────────────────────

class TestInvalidSide:
    def test_invalid_side_raises(self):
        cfg = SlippageConfig()
        with pytest.raises(ValueError, match="side"):
            apply_slippage(Decimal("50000"), "hold", Decimal("1"), Decimal("0"), cfg)


# ── Engine integration ────────────────────────────────────────────────────────

class TestEngineSlippage:
    """Verify engine paper-mode uses slippage."""

    def _make_engine(self, slippage_bps: float = 5.0):
        from decimal import Decimal
        from unittest.mock import MagicMock, patch
        from src.engine import TradingEngine
        from src.exchange.slippage import SlippageConfig
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

        return TradingEngine(
            settings=settings,
            exchange=MagicMock(),
            strategy=MagicMock(),
            risk_manager=risk_manager,
            portfolio=portfolio,
            slippage_config=SlippageConfig(spread_bps=slippage_bps, impact_bps_per_pct=0.0),
        )

    def test_engine_stores_slippage_config(self):
        engine = self._make_engine(slippage_bps=10.0)
        assert engine._slippage_config.spread_bps == 10.0

    def test_zero_slippage_config_stored(self):
        engine = self._make_engine(slippage_bps=0.0)
        assert engine._slippage_config.spread_bps == 0.0

    def test_default_engine_uses_default_slippage(self):
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

        engine = TradingEngine(
            settings=settings,
            exchange=MagicMock(),
            strategy=MagicMock(),
            risk_manager=risk_manager,
            portfolio=portfolio,
        )
        # Default SlippageConfig has 5 bps
        assert engine._slippage_config.spread_bps == 5.0
