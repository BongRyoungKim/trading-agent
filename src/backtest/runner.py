"""
Backtesting engine: simulate a strategy against historical OHLCV data.

Simulation assumptions:
- One position at a time (no pyramiding).
- Orders fill at the open of the next bar (avoids look-ahead bias).
- Commission applied as a percentage of trade notional.
- Slippage applied as a fixed percentage of price.
- Short selling not supported (buy-only strategy).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
from loguru import logger

from src.backtest import metrics as m
from src.backtest.models import BacktestResult, Trade
from src.strategy.base import BaseStrategy
from src.strategy.models import SignalAction
from src.utils.indicators import add_indicators


@dataclass
class BacktestConfig:
    """Configuration for a single backtest run."""

    initial_capital: Decimal = Decimal("10000")
    commission_pct: float = 0.001       # 0.1% per trade
    slippage_pct: float = 0.0005        # 0.05% price impact
    position_size_pct: float = 1.0      # fraction of capital per trade (1.0 = 100%)


class BacktestRunner:
    """
    Event-driven backtesting engine.

    Usage:
        runner = BacktestRunner(strategy, config)
        result = runner.run(ohlcv_df)
        print(result.summary())
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        config: BacktestConfig | None = None,
    ) -> None:
        self._strategy = strategy
        self._config = config or BacktestConfig()

    def run(self, data: pd.DataFrame, symbol: str = "UNKNOWN", timeframe: str = "1h") -> BacktestResult:
        """
        Run the backtest over the provided OHLCV DataFrame.

        Args:
            data:      OHLCV DataFrame with columns: timestamp, open, high, low, close, volume.
                       Oldest row first.
            symbol:    Market symbol label for the result.
            timeframe: Timeframe label for the result.

        Returns:
            BacktestResult with all metrics computed.
        """
        self._strategy.validate_data(data)
        data = data.reset_index(drop=True)

        min_bars = self._strategy.min_required_bars()
        config = self._config
        capital = float(config.initial_capital)
        trades: list[Trade] = []

        # State
        in_position = False
        entry_price = 0.0
        entry_time: datetime | None = None
        position_amount = 0.0

        logger.info(
            "Backtest starting",
            strategy=self._strategy.name,
            symbol=symbol,
            bars=len(data),
            initial_capital=float(config.initial_capital),
        )

        for i in range(min_bars, len(data)):
            window = data.iloc[: i + 1]
            signal = self._strategy.generate_signal(window)

            # Fill price is next bar's open (i+1), if available; else current close
            if i + 1 < len(data):
                fill_price = float(data["open"].iloc[i + 1])
            else:
                fill_price = float(data["close"].iloc[i])

            # Apply slippage
            if signal.action == SignalAction.BUY:
                fill_price *= 1 + config.slippage_pct
            elif signal.action == SignalAction.SELL:
                fill_price *= 1 - config.slippage_pct

            bar_time = (
                data["timestamp"].iloc[i]
                if "timestamp" in data.columns
                else datetime.now(UTC)
            )

            # Open position on BUY
            if signal.action == SignalAction.BUY and not in_position:
                trade_capital = capital * config.position_size_pct
                commission = trade_capital * config.commission_pct
                position_amount = (trade_capital - commission) / fill_price
                entry_price = fill_price
                entry_time = bar_time
                in_position = True

            # Close position on SELL
            elif signal.action == SignalAction.SELL and in_position:
                exit_commission = fill_price * position_amount * config.commission_pct
                trade = Trade(
                    symbol=symbol,
                    side="buy",
                    entry_price=Decimal(str(round(entry_price, 8))),
                    exit_price=Decimal(str(round(fill_price, 8))),
                    amount=Decimal(str(round(position_amount, 8))),
                    entry_time=entry_time,  # type: ignore[arg-type]
                    exit_time=bar_time,
                    commission=Decimal(str(round(exit_commission, 8))),
                )
                capital += float(trade.net_pnl)
                trades.append(trade)
                in_position = False

        # Force-close open position at last bar's close price
        if in_position:
            last_close = float(data["close"].iloc[-1])
            last_close *= 1 - config.slippage_pct
            last_time = (
                data["timestamp"].iloc[-1]
                if "timestamp" in data.columns
                else datetime.now(UTC)
            )
            exit_commission = last_close * position_amount * config.commission_pct
            trade = Trade(
                symbol=symbol,
                side="buy",
                entry_price=Decimal(str(round(entry_price, 8))),
                exit_price=Decimal(str(round(last_close, 8))),
                amount=Decimal(str(round(position_amount, 8))),
                entry_time=entry_time,  # type: ignore[arg-type]
                exit_time=last_time,
                commission=Decimal(str(round(exit_commission, 8))),
            )
            capital += float(trade.net_pnl)
            trades.append(trade)

        final_capital = Decimal(str(round(capital, 4)))
        start_date = data["timestamp"].iloc[0] if "timestamp" in data.columns else datetime.now(UTC)
        end_date = data["timestamp"].iloc[-1] if "timestamp" in data.columns else datetime.now(UTC)

        metrics = m.compute_all(
            initial_capital=config.initial_capital,
            final_capital=final_capital,
            trades=trades,
            start_date=start_date,
            end_date=end_date,
        )

        logger.info(
            "Backtest complete",
            strategy=self._strategy.name,
            total_trades=len(trades),
            total_return_pct=round(metrics["total_return_pct"], 2),
        )

        return BacktestResult(
            strategy_name=self._strategy.name,
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            initial_capital=config.initial_capital,
            final_capital=final_capital,
            trades=tuple(trades),
            **metrics,
        )
