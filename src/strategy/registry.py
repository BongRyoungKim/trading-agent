"""
Strategy registry: register and instantiate strategies by name.
"""
from __future__ import annotations

from src.strategy.base import BaseStrategy

_REGISTRY: dict[str, type[BaseStrategy]] = {}


def register(strategy_class: type[BaseStrategy]) -> type[BaseStrategy]:
    """
    Class decorator to register a strategy.

    Usage:
        @register
        class MyCoolStrategy(BaseStrategy):
            ...
    """
    _REGISTRY[strategy_class.__name__] = strategy_class
    return strategy_class


def get_strategy(name: str, **kwargs) -> BaseStrategy:  # type: ignore[no-untyped-def]
    """
    Instantiate a registered strategy by class name.

    Args:
        name:   Class name of the strategy, e.g. 'MACrossoverStrategy'.
        **kwargs: Constructor arguments forwarded to the strategy.

    Raises:
        KeyError: If no strategy with that name is registered.
    """
    if name not in _REGISTRY:
        available = list(_REGISTRY.keys())
        raise KeyError(
            f"Strategy '{name}' not found. Available: {available}"
        )
    return _REGISTRY[name](**kwargs)


def list_strategies() -> list[str]:
    """Return sorted list of all registered strategy names."""
    return sorted(_REGISTRY.keys())
