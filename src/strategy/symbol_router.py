"""
Per-symbol strategy router.

Maps symbols to dedicated strategy instances, with an optional default
fallback for unrouted symbols.  Designed to be passed directly to
``TradingEngine`` as a callable ``StrategyProvider``.

Usage::

    router = SymbolStrategyRouter(
        routes={"BTC/USDT": btc_strategy, "ETH/USDT": eth_strategy},
        default=fallback_strategy,
    )
    engine = TradingEngine(..., strategy=router)
"""
from __future__ import annotations

from src.strategy.base import BaseStrategy, StrategyFactory


class SymbolStrategyRouter(StrategyFactory):
    """
    Callable strategy provider that routes each symbol to a dedicated strategy.

    Args:
        routes:  Explicit ``{symbol: strategy}`` mappings.
        default: Fallback strategy for symbols not in *routes*.
                 If ``None`` and the symbol is not in *routes*,
                 ``__call__`` raises ``KeyError``.

    Raises:
        ValueError: When both *routes* is empty and *default* is ``None``.
    """

    def __init__(
        self,
        routes: dict[str, BaseStrategy],
        default: BaseStrategy | None = None,
    ) -> None:
        if not routes and default is None:
            raise ValueError(
                "SymbolStrategyRouter requires at least one route or a default strategy"
            )
        self._routes: dict[str, BaseStrategy] = dict(routes)
        self._default = default

    # ── Callable interface (used by TradingEngine) ────────────────────────────

    def __call__(self, symbol: str) -> BaseStrategy:
        """Return the strategy for *symbol*, falling back to the default."""
        if symbol in self._routes:
            return self._routes[symbol]
        if self._default is not None:
            return self._default
        raise KeyError(
            f"No strategy mapped for symbol '{symbol}' and no default set"
        )

    # ── Introspection helpers ─────────────────────────────────────────────────

    def strategy_names(self) -> dict[str, str]:
        """Return ``{symbol: class_name}`` mapping for display / logging."""
        result = {sym: type(strat).__name__ for sym, strat in self._routes.items()}
        if self._default is not None:
            result["*"] = type(self._default).__name__
        return result

    @property
    def routes(self) -> dict[str, BaseStrategy]:
        """A copy of the explicit symbol → strategy mapping."""
        return dict(self._routes)

    @property
    def default(self) -> BaseStrategy | None:
        """The fallback strategy, or ``None`` if not set."""
        return self._default
