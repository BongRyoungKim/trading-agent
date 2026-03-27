"""
Abstract base class for all trading strategies.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from src.strategy.models import Signal


class StrategyFactory(ABC):
    """
    Marker base class for callable strategy factories.

    Subclass this (alongside making the class callable) so that
    ``TradingEngine`` can distinguish real factories from plain
    ``BaseStrategy`` instances or test mocks.
    """

    @abstractmethod
    def __call__(self, symbol: str) -> "BaseStrategy":
        """Return the strategy to use for *symbol*."""


class BaseStrategy(ABC):
    """
    Every trading strategy must implement this interface.

    Convention:
        - `generate_signal` receives a DataFrame with OHLCV columns
          (open, high, low, close, volume) and any pre-computed indicators.
        - Return a Signal with action=HOLD when no trade is warranted.
        - Never raise exceptions for normal market conditions; only raise
          for invalid input (e.g. insufficient data).

    Usage:
        class MyStrategy(BaseStrategy):
            @property
            def name(self) -> str:
                return "my_strategy"

            def generate_signal(self, data: pd.DataFrame) -> Signal:
                ...

            def get_parameters(self) -> dict:
                return {}
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy identifier, e.g. 'ma_crossover_20_50'."""

    @abstractmethod
    def generate_signal(self, data: pd.DataFrame) -> Signal:
        """
        Analyse market data and return a Signal.

        Args:
            data: OHLCV DataFrame (oldest row first).
                  Must contain columns: open, high, low, close, volume, timestamp.
                  May contain additional pre-computed indicator columns.

        Returns:
            Signal with the recommended action.

        Raises:
            StrategyError: If data is insufficient or malformed.
        """

    @abstractmethod
    def get_parameters(self) -> dict:
        """Return the strategy's current parameter configuration."""

    @property
    def timeframe(self) -> str:
        """
        OHLCV candle interval this strategy expects.
        Engines use this value when calling ``get_ohlcv_dataframe()``.
        Override in subclasses to request a different resolution.
        Common values: '1m', '5m', '15m', '1h', '4h', '1d'.
        """
        return "1h"

    def min_required_bars(self) -> int:
        """
        Minimum number of bars needed before the strategy can produce a signal.
        Override in subclasses. Default: 1.
        """
        return 1

    def validate_data(self, data: pd.DataFrame) -> None:
        """
        Raise StrategyError if data does not meet minimum requirements.
        Called automatically before generate_signal.
        """
        from src.utils.exceptions import StrategyError

        required_cols = {"open", "high", "low", "close", "volume"}
        missing = required_cols - set(data.columns)
        if missing:
            raise StrategyError(
                f"Missing required columns: {missing}",
                details={"strategy": self.name, "missing_cols": str(missing)},
            )
        if len(data) < self.min_required_bars():
            raise StrategyError(
                f"Insufficient data: need {self.min_required_bars()} bars, got {len(data)}",
                details={"strategy": self.name, "required": self.min_required_bars(), "got": len(data)},
            )
