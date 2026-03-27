"""
Backtest CLI: run a strategy against historical data from the local SQLite store
or download fresh data from the exchange.

Usage examples:
    python -m src.backtest_cli --symbol BTC/USDT --timeframe 1h --strategy ma_crossover
    python -m src.backtest_cli --symbol BTC/USDT --optimize --rank-by sharpe_ratio
    python -m src.backtest_cli --symbol BTC/USDT --walk-forward --is-bars 500 --oos-bars 100
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal

from loguru import logger

from src.config.settings import get_settings
from src.utils.logger import setup_logger


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.backtest_cli",
        description="Trading Agent — Backtest CLI",
    )
    p.add_argument("--symbol", default="BTC/USDT", help="Trading pair (default: BTC/USDT)")
    p.add_argument("--timeframe", default="1h", help="OHLCV timeframe (default: 1h)")
    p.add_argument("--bars", type=int, default=1000, help="Number of historical bars (default: 1000)")
    p.add_argument("--capital", type=float, default=10000.0, help="Initial capital (default: 10000)")
    p.add_argument("--commission", type=float, default=0.001, help="Commission rate (default: 0.001)")
    p.add_argument("--strategy", default="ma_crossover_ema_20_50",
                   help="Strategy name from registry (default: ma_crossover_ema_20_50)")

    # Optimization flags
    p.add_argument("--optimize", action="store_true", help="Run grid search optimization")
    p.add_argument("--rank-by", default="sharpe_ratio",
                   help="Metric to rank optimization results (default: sharpe_ratio)")
    p.add_argument("--top-n", type=int, default=5, help="Show top N optimization results")

    # Walk-forward flags
    p.add_argument("--walk-forward", action="store_true", help="Run walk-forward analysis")
    p.add_argument("--is-bars", type=int, default=500, help="In-sample window bars (default: 500)")
    p.add_argument("--oos-bars", type=int, default=100, help="Out-of-sample window bars (default: 100)")
    p.add_argument("--anchored", action="store_true", help="Use anchored (expanding) IS window")

    # Output flags
    p.add_argument("--save-chart", type=str, default="",
                   help="Save result chart to this path (e.g. reports/bt.png)")

    return p


def _load_data(symbol: str, timeframe: str, bars: int):
    """Load OHLCV data from exchange (paper mode, no auth needed)."""
    from src.exchange.binance import BinanceClient
    settings = get_settings()
    client = BinanceClient(api_key="", secret_key="", sandbox=True)
    logger.info("Fetching data", symbol=symbol, timeframe=timeframe, bars=bars)
    df = client.get_ohlcv_dataframe(symbol, timeframe=timeframe, limit=bars)
    logger.info("Data loaded", rows=len(df))
    return df


def _run_single(args, data) -> None:
    from src.backtest.runner import BacktestConfig, BacktestRunner
    from src.strategy.registry import get_strategy

    strategy = get_strategy(args.strategy, symbol=args.symbol)
    config = BacktestConfig(
        initial_capital=Decimal(str(args.capital)),
        commission_pct=args.commission,
    )
    runner = BacktestRunner(strategy, config)
    result = runner.run(data)
    print("\n" + "=" * 60)
    print(result.summary())
    print("=" * 60)

    if args.save_chart:
        from src.backtest.visualizer import BacktestVisualizer
        viz = BacktestVisualizer(result)
        path = viz.save(args.save_chart)
        print(f"\nChart saved → {path}")


def _run_optimize(args, data) -> None:
    from src.backtest.optimizer import StrategyOptimizer
    from src.backtest.runner import BacktestConfig
    from src.strategy.ma_crossover import MACrossoverStrategy

    config = BacktestConfig(
        initial_capital=Decimal(str(args.capital)),
        commission_pct=args.commission,
    )
    optimizer = StrategyOptimizer(
        strategy_class=MACrossoverStrategy,
        fixed_params={"symbol": args.symbol},
        param_grid={
            "fast_period": [5, 10, 20],
            "slow_period": [20, 50, 100, 200],
            "ma_type": ["ema", "sma"],
        },
        config=config,
        rank_by=args.rank_by,
        min_trades=3,
    )
    results = optimizer.run(data)
    print(f"\nTop {args.top_n} results (ranked by {args.rank_by}):")
    for i, r in enumerate(results[: args.top_n], 1):
        print(f"  {i}. {r}")


def _run_walk_forward(args, data) -> None:
    from src.backtest.runner import BacktestConfig
    from src.backtest.walk_forward import WalkForwardAnalyzer
    from src.strategy.registry import get_strategy

    strategy = get_strategy(args.strategy, symbol=args.symbol)
    config = BacktestConfig(
        initial_capital=Decimal(str(args.capital)),
        commission_pct=args.commission,
    )
    wfa = WalkForwardAnalyzer(
        strategy=strategy,
        config=config,
        is_bars=args.is_bars,
        oos_bars=args.oos_bars,
        anchored=args.anchored,
    )
    report = wfa.run(data)
    print("\n" + "=" * 60)
    print(report.summary())
    print("=" * 60)


def main() -> None:
    setup_logger()
    parser = _build_parser()
    args = parser.parse_args()

    try:
        data = _load_data(args.symbol, args.timeframe, args.bars)
    except Exception as exc:
        logger.error("Failed to load data", error=str(exc))
        sys.exit(1)

    if args.optimize:
        _run_optimize(args, data)
    elif args.walk_forward:
        _run_walk_forward(args, data)
    else:
        _run_single(args, data)


if __name__ == "__main__":
    main()
