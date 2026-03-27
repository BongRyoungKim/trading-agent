# Import strategies to trigger @register decorator
from src.strategy.bollinger import BollingerBandStrategy
from src.strategy.composite import CompositeStrategy
from src.strategy.ma_crossover import MACrossoverStrategy
from src.strategy.models import Signal, SignalAction
from src.strategy.momentum import MomentumStrategy
from src.strategy.registry import get_strategy, list_strategies, register
from src.strategy.rsi_momentum import RSIMomentumStrategy
from src.strategy.rsi_strategy import RSIStrategy
from src.strategy.scalping_5m import Scalping5mStrategy
from src.strategy.vwap_strategy import VWAPStrategy

__all__ = [
    "Signal",
    "SignalAction",
    "MACrossoverStrategy",
    "RSIStrategy",
    "BollingerBandStrategy",
    "CompositeStrategy",
    "VWAPStrategy",
    "MomentumStrategy",
    "RSIMomentumStrategy",
    "Scalping5mStrategy",
    "register",
    "get_strategy",
    "list_strategies",
]
