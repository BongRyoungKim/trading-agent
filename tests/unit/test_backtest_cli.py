"""Unit tests for src/backtest_cli.py."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.backtest_cli import _build_parser


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_df(n: int = 100) -> pd.DataFrame:
    import numpy as np
    from datetime import UTC, datetime, timedelta
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return pd.DataFrame(
        {
            "open": np.full(n, 50000.0),
            "high": np.full(n, 51000.0),
            "low": np.full(n, 49000.0),
            "close": np.full(n, 50000.0),
            "volume": np.full(n, 100.0),
        },
        index=pd.DatetimeIndex([base + timedelta(hours=i) for i in range(n)]),
    )


# ── Parser ────────────────────────────────────────────────────────────────────

class TestBuildParser:
    def test_defaults(self):
        args = _build_parser().parse_args([])
        assert args.symbol == "BTC/USDT"
        assert args.timeframe == "1h"
        assert args.bars == 1000
        assert args.capital == 10000.0
        assert args.commission == 0.001
        assert args.optimize is False
        assert args.walk_forward is False

    def test_symbol_override(self):
        args = _build_parser().parse_args(["--symbol", "ETH/USDT"])
        assert args.symbol == "ETH/USDT"

    def test_optimize_flag(self):
        assert _build_parser().parse_args(["--optimize"]).optimize is True

    def test_walk_forward_flag(self):
        assert _build_parser().parse_args(["--walk-forward"]).walk_forward is True

    def test_save_chart_default_empty(self):
        assert _build_parser().parse_args([]).save_chart == ""

    def test_anchored_flag(self):
        assert _build_parser().parse_args(["--walk-forward", "--anchored"]).anchored is True


# ── _run_single ───────────────────────────────────────────────────────────────

def _args_single(strategy="ma_crossover_ema_20_50", symbol="BTC/USDT",
                 capital=10000.0, commission=0.001, save_chart=""):
    a = MagicMock()
    a.strategy = strategy
    a.symbol = symbol
    a.capital = capital
    a.commission = commission
    a.save_chart = save_chart
    return a


class TestRunSingle:
    def test_run_single_prints_summary(self, capsys):
        mock_result = MagicMock()
        mock_result.summary.return_value = "=== Backtest Summary ==="
        with (
            patch("src.strategy.registry.get_strategy"),
            patch("src.backtest.runner.BacktestRunner") as MockRunner,
        ):
            MockRunner.return_value.run.return_value = mock_result
            from src.backtest_cli import _run_single
            _run_single(_args_single(), _make_df())
        assert "Backtest Summary" in capsys.readouterr().out

    def test_run_single_saves_chart_when_path_given(self, tmp_path):
        mock_result = MagicMock()
        mock_result.summary.return_value = "Summary"
        chart_path = str(tmp_path / "chart.png")
        with (
            patch("src.strategy.registry.get_strategy"),
            patch("src.backtest.runner.BacktestRunner") as MockRunner,
            patch("src.backtest.visualizer.BacktestVisualizer") as MockViz,
        ):
            MockRunner.return_value.run.return_value = mock_result
            MockViz.return_value.save.return_value = chart_path
            from src.backtest_cli import _run_single
            _run_single(_args_single(save_chart=chart_path), _make_df())
        MockViz.assert_called_once()

    def test_run_single_no_chart_when_path_empty(self):
        mock_result = MagicMock()
        mock_result.summary.return_value = "Summary"
        with (
            patch("src.strategy.registry.get_strategy"),
            patch("src.backtest.runner.BacktestRunner") as MockRunner,
            patch("src.backtest.visualizer.BacktestVisualizer") as MockViz,
        ):
            MockRunner.return_value.run.return_value = mock_result
            from src.backtest_cli import _run_single
            _run_single(_args_single(save_chart=""), _make_df())
        MockViz.assert_not_called()


# ── _run_optimize ─────────────────────────────────────────────────────────────

def _args_opt(symbol="BTC/USDT", capital=10000.0, commission=0.001,
              rank_by="sharpe_ratio", top_n=3):
    a = MagicMock()
    a.symbol = symbol
    a.capital = capital
    a.commission = commission
    a.rank_by = rank_by
    a.top_n = top_n
    return a


class TestRunOptimize:
    def test_run_optimize_calls_optimizer(self, capsys):
        mock_r = MagicMock()
        mock_r.__str__ = lambda self: "Result()"
        with patch("src.backtest.optimizer.StrategyOptimizer") as MockOpt:
            MockOpt.return_value.run.return_value = [mock_r, mock_r]
            from src.backtest_cli import _run_optimize
            _run_optimize(_args_opt(top_n=2), _make_df())
        MockOpt.assert_called_once()
        assert "sharpe_ratio" in capsys.readouterr().out

    def test_run_optimize_respects_top_n(self, capsys):
        results = [MagicMock(__str__=lambda self: f"R") for _ in range(10)]
        with patch("src.backtest.optimizer.StrategyOptimizer") as MockOpt:
            MockOpt.return_value.run.return_value = results
            from src.backtest_cli import _run_optimize
            _run_optimize(_args_opt(top_n=3), _make_df())
        out = capsys.readouterr().out
        assert "1." in out
        assert "4." not in out


# ── _run_walk_forward ─────────────────────────────────────────────────────────

def _args_wf(strategy="ma_crossover_ema_20_50", symbol="BTC/USDT",
             capital=10000.0, commission=0.001, is_bars=200, oos_bars=50, anchored=False):
    a = MagicMock()
    a.strategy = strategy
    a.symbol = symbol
    a.capital = capital
    a.commission = commission
    a.is_bars = is_bars
    a.oos_bars = oos_bars
    a.anchored = anchored
    return a


class TestRunWalkForward:
    def test_run_walk_forward_prints_report(self, capsys):
        mock_report = MagicMock()
        mock_report.summary.return_value = "=== Walk-Forward ==="
        with (
            patch("src.strategy.registry.get_strategy"),
            patch("src.backtest.walk_forward.WalkForwardAnalyzer") as MockWFA,
        ):
            MockWFA.return_value.run.return_value = mock_report
            from src.backtest_cli import _run_walk_forward
            _run_walk_forward(_args_wf(), _make_df())
        assert "Walk-Forward" in capsys.readouterr().out


# ── main() ────────────────────────────────────────────────────────────────────

def _run_cli_main(argv: list[str]):
    old_argv = sys.argv
    sys.argv = ["backtest_cli"] + argv
    try:
        from src.backtest_cli import main
        main()
    finally:
        sys.argv = old_argv


class TestBacktestCliMain:
    def test_main_single_mode(self, capsys):
        mock_result = MagicMock()
        mock_result.summary.return_value = "BacktestResult"
        with (
            patch("src.backtest_cli.setup_logger"),
            patch("src.backtest_cli._load_data", return_value=_make_df()),
            patch("src.strategy.registry.get_strategy"),
            patch("src.backtest.runner.BacktestRunner") as MockRunner,
        ):
            MockRunner.return_value.run.return_value = mock_result
            _run_cli_main([])
        assert "BacktestResult" in capsys.readouterr().out

    def test_main_exits_on_data_load_failure(self):
        with (
            patch("src.backtest_cli.setup_logger"),
            patch("src.backtest_cli._load_data", side_effect=Exception("network")),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_cli_main([])
        assert exc_info.value.code == 1

    def test_main_optimize_mode(self):
        with (
            patch("src.backtest_cli.setup_logger"),
            patch("src.backtest_cli._load_data", return_value=_make_df()),
            patch("src.backtest_cli._run_optimize") as mock_opt,
        ):
            _run_cli_main(["--optimize"])
        mock_opt.assert_called_once()

    def test_main_walk_forward_mode(self):
        with (
            patch("src.backtest_cli.setup_logger"),
            patch("src.backtest_cli._load_data", return_value=_make_df()),
            patch("src.backtest_cli._run_walk_forward") as mock_wf,
        ):
            _run_cli_main(["--walk-forward"])
        mock_wf.assert_called_once()
