"""Unit tests for BacktestVisualizer."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.backtest.models import BacktestResult, Trade
from src.backtest.visualizer import BacktestVisualizer


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_trade(
    entry_price: float,
    exit_price: float,
    amount: float = 0.1,
    offset_days: int = 0,
) -> Trade:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return Trade(
        symbol="BTC/USDT",
        side="buy",
        entry_price=Decimal(str(entry_price)),
        exit_price=Decimal(str(exit_price)),
        amount=Decimal(str(amount)),
        entry_time=base + timedelta(days=offset_days),
        exit_time=base + timedelta(days=offset_days + 1),
        commission=Decimal("0"),
    )


def _make_result(trades: list[Trade] | None = None) -> BacktestResult:
    t = trades or []
    initial = Decimal("10000")
    pnl = sum(tr.net_pnl for tr in t)
    return BacktestResult(
        strategy_name="test_strategy",
        symbol="BTC/USDT",
        timeframe="1h",
        start_date=datetime(2024, 1, 1, tzinfo=UTC),
        end_date=datetime(2024, 3, 1, tzinfo=UTC),
        initial_capital=initial,
        final_capital=initial + pnl,
        trades=tuple(t),
        total_return_pct=float(pnl / initial * 100),
        max_drawdown_pct=5.0,
        sharpe_ratio=1.2,
        win_rate_pct=60.0,
        total_trades=len(t),
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestBacktestVisualizerEquityData:
    def test_equity_curve_data_empty_trades(self):
        result = _make_result([])
        viz = BacktestVisualizer(result)
        ts, vals = viz.equity_curve_data()
        assert ts == []
        assert vals == []

    def test_equity_curve_data_single_trade(self):
        trade = _make_trade(50000.0, 55000.0, 0.1)
        result = _make_result([trade])
        viz = BacktestVisualizer(result)
        ts, vals = viz.equity_curve_data()
        # start_date + 1 exit point = 2 points
        assert len(ts) == 2
        assert len(vals) == 2
        assert vals[0] == float(result.initial_capital)
        assert vals[-1] > vals[0]  # profitable trade

    def test_equity_curve_data_losing_trade(self):
        trade = _make_trade(55000.0, 50000.0, 0.1)
        result = _make_result([trade])
        viz = BacktestVisualizer(result)
        ts, vals = viz.equity_curve_data()
        assert vals[-1] < vals[0]  # losing trade

    def test_equity_curve_data_multiple_trades(self):
        trades = [
            _make_trade(50000.0, 52000.0, 0.1, offset_days=0),
            _make_trade(52000.0, 51000.0, 0.1, offset_days=5),
            _make_trade(51000.0, 54000.0, 0.1, offset_days=10),
        ]
        result = _make_result(trades)
        viz = BacktestVisualizer(result)
        ts, vals = viz.equity_curve_data()
        # 1 start + 3 trade points
        assert len(ts) == 4
        assert len(vals) == 4


class TestBacktestVisualizerMissingMatplotlib:
    def test_plot_raises_import_error(self):
        result = _make_result()
        viz = BacktestVisualizer(result)
        with patch("src.backtest.visualizer._require_matplotlib",
                   side_effect=ImportError("no matplotlib")):
            with pytest.raises(ImportError, match="no matplotlib"):
                viz.plot()

    def test_save_raises_import_error(self, tmp_path):
        result = _make_result()
        viz = BacktestVisualizer(result)
        with patch("src.backtest.visualizer._require_matplotlib",
                   side_effect=ImportError("no matplotlib")):
            with pytest.raises(ImportError, match="no matplotlib"):
                viz.save(tmp_path / "test.png")


class TestBacktestVisualizerSave:
    def test_save_creates_parent_dir(self, tmp_path):
        result = _make_result([_make_trade(50000.0, 52000.0)])
        viz = BacktestVisualizer(result)

        mock_fig = MagicMock()
        mock_plt = MagicMock()
        mock_plt.subplots.return_value = (mock_fig, [MagicMock(), MagicMock(), MagicMock()])

        with patch("src.backtest.visualizer._require_matplotlib",
                   return_value=(MagicMock(), mock_plt)):
            with patch.object(viz, "_build_figure", return_value=mock_fig):
                dest = viz.save(tmp_path / "subdir" / "chart.png")

        assert dest == tmp_path / "subdir" / "chart.png"
        mock_fig.savefig.assert_called_once()

    def test_save_returns_path(self, tmp_path):
        result = _make_result([_make_trade(50000.0, 52000.0)])
        viz = BacktestVisualizer(result)
        mock_fig = MagicMock()
        mock_plt = MagicMock()

        with patch("src.backtest.visualizer._require_matplotlib",
                   return_value=(MagicMock(), mock_plt)):
            with patch.object(viz, "_build_figure", return_value=mock_fig):
                dest = viz.save(tmp_path / "out.png")

        assert isinstance(dest, Path)
