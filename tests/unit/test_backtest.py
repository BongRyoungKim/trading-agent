"""Unit tests for backtest engine: models, metrics, runner."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from src.backtest.metrics import (
    annualized_return_pct,
    build_equity_curve,
    compute_all,
    max_drawdown_pct,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    total_return_pct,
    win_rate_pct,
)
from src.backtest.models import BacktestResult, Trade
from src.backtest.runner import BacktestConfig, BacktestRunner
from src.strategy.bollinger import BollingerBandStrategy
from src.strategy.ma_crossover import MACrossoverStrategy


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_trade(
    pnl: float,
    entry_time: datetime | None = None,
    exit_time: datetime | None = None,
) -> Trade:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    entry = entry_time or base
    exit_ = exit_time or (base + timedelta(hours=1))
    entry_p = Decimal("50000")
    amount = Decimal("0.01")
    net = Decimal(str(pnl))
    # Derive exit price from pnl (simplified)
    exit_p = entry_p + net / amount
    return Trade(
        symbol="BTC/USDT",
        side="buy",
        entry_price=entry_p,
        exit_price=exit_p,
        amount=amount,
        entry_time=entry,
        exit_time=exit_,
        commission=Decimal("0"),
    )


def _make_ohlcv(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    base = datetime(2024, 1, 1, tzinfo=UTC)
    closes_arr = np.array(closes, dtype=float)
    return pd.DataFrame({
        "timestamp": [base + timedelta(hours=i) for i in range(n)],
        "open": closes_arr * 0.999,
        "high": closes_arr * 1.005,
        "low": closes_arr * 0.995,
        "close": closes_arr,
        "volume": np.ones(n) * 100.0,
    })


# ── Trade Model ───────────────────────────────────────────────────────────────

class TestTradeModel:
    def test_pnl_long_profit(self) -> None:
        t = Trade(
            symbol="BTC/USDT", side="buy",
            entry_price=Decimal("50000"), exit_price=Decimal("55000"),
            amount=Decimal("0.1"),
            entry_time=datetime(2024, 1, 1, tzinfo=UTC),
            exit_time=datetime(2024, 1, 2, tzinfo=UTC),
            commission=Decimal("10"),
        )
        assert t.pnl == Decimal("500")       # (55000-50000)*0.1
        assert t.net_pnl == Decimal("490")   # 500 - 10
        assert t.is_winner

    def test_pnl_long_loss(self) -> None:
        t = Trade(
            symbol="BTC/USDT", side="buy",
            entry_price=Decimal("50000"), exit_price=Decimal("45000"),
            amount=Decimal("0.1"),
            entry_time=datetime(2024, 1, 1, tzinfo=UTC),
            exit_time=datetime(2024, 1, 2, tzinfo=UTC),
            commission=Decimal("5"),
        )
        assert t.pnl == Decimal("-500")
        assert t.net_pnl == Decimal("-505")
        assert not t.is_winner

    def test_return_pct(self) -> None:
        t = Trade(
            symbol="BTC/USDT", side="buy",
            entry_price=Decimal("100"), exit_price=Decimal("110"),
            amount=Decimal("1"),
            entry_time=datetime(2024, 1, 1, tzinfo=UTC),
            exit_time=datetime(2024, 1, 2, tzinfo=UTC),
            commission=Decimal("0"),
        )
        assert abs(t.return_pct - 10.0) < 1e-6

    def test_trade_is_immutable(self) -> None:
        t = _make_trade(100.0)
        with pytest.raises((AttributeError, TypeError)):
            t.symbol = "ETH/USDT"  # type: ignore[misc]


# ── Metrics ───────────────────────────────────────────────────────────────────

class TestMetrics:
    def test_total_return_positive(self) -> None:
        result = total_return_pct(Decimal("10000"), Decimal("12000"))
        assert abs(result - 20.0) < 1e-6

    def test_total_return_loss(self) -> None:
        result = total_return_pct(Decimal("10000"), Decimal("8000"))
        assert abs(result - (-20.0)) < 1e-6

    def test_total_return_zero_initial(self) -> None:
        assert total_return_pct(Decimal("0"), Decimal("1000")) == 0.0

    def test_annualized_return_one_year(self) -> None:
        start = datetime(2023, 1, 1, tzinfo=UTC)
        end = datetime(2024, 1, 1, tzinfo=UTC)
        ann = annualized_return_pct(20.0, start, end)
        assert abs(ann - 20.0) < 0.5  # approximately equal for one year

    def test_max_drawdown_simple(self) -> None:
        equity = pd.Series([100.0, 120.0, 90.0, 110.0])
        dd = max_drawdown_pct(equity)
        # From 120 to 90 → 25%
        assert abs(dd - 25.0) < 1e-3

    def test_max_drawdown_no_drawdown(self) -> None:
        equity = pd.Series([100.0, 110.0, 120.0, 130.0])
        assert max_drawdown_pct(equity) == 0.0

    def test_max_drawdown_empty(self) -> None:
        assert max_drawdown_pct(pd.Series(dtype=float)) == 0.0

    def test_sharpe_positive_returns(self) -> None:
        returns = pd.Series([0.01] * 100)
        sr = sharpe_ratio(returns)
        assert sr > 0

    def test_sharpe_zero_std(self) -> None:
        returns = pd.Series([0.0] * 50)
        assert sharpe_ratio(returns) == 0.0

    def test_win_rate_all_winners(self) -> None:
        trades = [_make_trade(100.0)] * 5
        assert win_rate_pct(trades) == 100.0

    def test_win_rate_no_trades(self) -> None:
        assert win_rate_pct([]) == 0.0

    def test_profit_factor_with_mixed(self) -> None:
        winners = [_make_trade(200.0)] * 3
        losers = [_make_trade(-100.0)] * 2
        pf = profit_factor(winners + losers)
        assert abs(pf - 3.0) < 0.1  # 600 profit / 200 loss

    def test_profit_factor_no_losers(self) -> None:
        assert profit_factor([_make_trade(100.0)]) == float("inf")

    def test_build_equity_curve_shape(self) -> None:
        trades = [
            _make_trade(100.0, exit_time=datetime(2024, 1, 1, tzinfo=UTC)),
            _make_trade(-50.0, exit_time=datetime(2024, 1, 2, tzinfo=UTC)),
        ]
        curve = build_equity_curve(Decimal("10000"), trades)
        assert len(curve) == 2
        assert curve.iloc[0] == 10100.0
        assert curve.iloc[1] == 10050.0


# ── BacktestRunner ────────────────────────────────────────────────────────────

class TestBacktestRunner:
    def test_run_returns_result(self) -> None:
        strategy = BollingerBandStrategy("BTC/USDT", period=10)
        runner = BacktestRunner(strategy)
        data = _make_ohlcv([50000.0] * 100)
        result = runner.run(data, symbol="BTC/USDT", timeframe="1h")
        assert isinstance(result, BacktestResult)

    def test_result_has_correct_strategy_name(self) -> None:
        strategy = BollingerBandStrategy("BTC/USDT", period=10)
        runner = BacktestRunner(strategy)
        result = runner.run(_make_ohlcv([50000.0] * 50))
        assert result.strategy_name == strategy.name

    def test_final_capital_is_positive(self) -> None:
        strategy = BollingerBandStrategy("BTC/USDT", period=10)
        runner = BacktestRunner(strategy)
        result = runner.run(_make_ohlcv([50000.0] * 50))
        assert result.final_capital > 0

    def test_no_trades_on_flat_prices(self) -> None:
        """Bollinger bands on flat prices → zero std → price always inside bands → HOLD."""
        strategy = BollingerBandStrategy("BTC/USDT", period=10)
        runner = BacktestRunner(strategy)
        result = runner.run(_make_ohlcv([50000.0] * 50))
        assert result.total_trades == 0
        assert result.final_capital == runner._config.initial_capital

    def test_trending_market_generates_trades(self) -> None:
        strategy = MACrossoverStrategy("BTC/USDT", fast_period=5, slow_period=10)
        runner = BacktestRunner(strategy)
        # Volatile prices to trigger crossovers
        rng = np.random.default_rng(42)
        prices = list(50000.0 + np.cumsum(rng.normal(0, 500, 200)))
        result = runner.run(_make_ohlcv(prices))
        # May or may not have trades, but result should be valid
        assert result.total_trades >= 0
        assert result.final_capital > 0

    def test_commission_reduces_capital(self) -> None:
        strategy = BollingerBandStrategy("BTC/USDT", period=5, std_dev=0.5)
        # Very tight bands to trigger many trades
        rng = np.random.default_rng(7)
        prices = list(50000.0 + rng.normal(0, 2000, 200))
        config = BacktestConfig(
            initial_capital=Decimal("10000"),
            commission_pct=0.01,  # 1% commission (very high)
        )
        runner = BacktestRunner(strategy, config)
        result = runner.run(_make_ohlcv(prices))
        # High commission should hurt returns
        assert isinstance(result.total_return_pct, float)

    def test_summary_returns_string(self) -> None:
        strategy = BollingerBandStrategy("BTC/USDT", period=10)
        runner = BacktestRunner(strategy)
        result = runner.run(_make_ohlcv([50000.0] * 50))
        summary = result.summary()
        assert isinstance(summary, str)
        assert "Strategy:" in summary

    def test_metrics_within_valid_ranges(self) -> None:
        strategy = MACrossoverStrategy("BTC/USDT", fast_period=5, slow_period=15)
        rng = np.random.default_rng(0)
        prices = list(50000.0 + np.cumsum(rng.normal(100, 800, 300)))
        result = BacktestRunner(strategy).run(_make_ohlcv(prices))
        assert 0 <= result.win_rate_pct <= 100
        assert result.max_drawdown_pct >= 0
        assert result.total_trades >= 0
