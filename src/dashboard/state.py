"""
DashboardState: thread-safe bridge between TradingEngine and the web dashboard.

The engine registers itself here at startup. FastAPI route handlers read state
through this singleton — keeping the web layer decoupled from the engine.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.engine import TradingEngine


class DashboardState:
    """Singleton that holds an optional reference to the running TradingEngine."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engine: TradingEngine | None = None

    def register_engine(self, engine: TradingEngine) -> None:
        with self._lock:
            self._engine = engine

    @property
    def engine(self) -> TradingEngine | None:
        with self._lock:
            return self._engine

    def get_status(self) -> dict:
        eng = self.engine
        if eng is None:
            return {"ready": False, "reason": "Engine not registered"}
        cb = eng.circuit_breaker
        cb_state = getattr(cb, "state", None)
        return {
            "ready": True,
            "mode": eng.mode,
            "paused": eng.is_paused,
            "circuit": cb_state.name if cb_state is not None else "UNKNOWN",
            "market_hours_enabled": eng.market_hours.enabled,
            "trading_hours": eng.market_hours.trading_hours if eng.market_hours.enabled else "24/7",
            "trading_days": eng.market_hours.trading_days if eng.market_hours.enabled else "all",
        }

    def get_positions(self) -> list[dict]:
        eng = self.engine
        if eng is None:
            return []
        report = eng._portfolio.pnl_report()  # noqa: SLF001
        positions = []
        for sym in eng._portfolio.open_symbols():  # noqa: SLF001
            pos = eng._portfolio.get_position(sym)
            if pos is None:
                continue
            positions.append({
                "symbol": sym,
                "side": pos.side,
                "amount": float(pos.amount),
                "entry_price": float(pos.entry_price),
                "stop_loss": float(pos.stop_loss) if pos.stop_loss is not None else None,
                "take_profit": float(pos.take_profit) if pos.take_profit is not None else None,
            })
        return positions

    def get_pnl(self) -> dict:
        eng = self.engine
        if eng is None:
            return {}
        report = eng._portfolio.pnl_report()  # noqa: SLF001
        return {
            "cash": float(report["cash"]),
            "realized_pnl": float(report["realized_pnl"]),
            "unrealized_pnl": float(report.get("unrealized_pnl", 0)),
            "open_positions": report["open_positions"],
        }

    def get_trades(self, limit: int = 50) -> list[dict]:
        """Return the most recent closed trades from the journal."""
        eng = self.engine
        if eng is None:
            return []
        journal = eng.journal  # noqa: SLF001
        trades = journal.recent(limit)
        result = []
        for t in reversed(trades):  # most recent first
            result.append({
                "symbol": t.symbol,
                "side": t.side,
                "amount": float(t.amount),
                "entry_price": float(t.entry_price),
                "exit_price": float(t.exit_price),
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "pnl": float(t.pnl),
                "pnl_pct": round(t.pnl_pct, 2),
                "commission": float(t.commission),
                "reason": t.reason,
                "is_win": t.is_win,
            })
        return result

    def get_stats(self) -> dict:
        """Return journal statistics (win rate, profit factor, etc.)."""
        eng = self.engine
        if eng is None:
            return {}
        return eng.journal.stats()  # noqa: SLF001

    def get_equity_curve(self) -> list[dict]:
        """Return cumulative PnL data points for charting."""
        eng = self.engine
        if eng is None:
            return []
        trades = eng.journal.trades  # noqa: SLF001
        curve = []
        cumulative = 0.0
        for t in trades:
            cumulative += float(t.pnl)
            curve.append({
                "time": t.exit_time.isoformat(),
                "pnl": round(cumulative, 4),
            })
        return curve

    def get_ticks(self) -> list[dict]:
        """Return latest tick evaluation result per symbol, sorted by symbol."""
        eng = self.engine
        if eng is None:
            return []
        ticks = getattr(eng, "_latest_ticks", {})
        return sorted(ticks.values(), key=lambda x: x["symbol"])

    def get_balance(self) -> list[dict]:
        """Return tradeable coin balances (non-KRW, free > 0, active KRW market)."""
        eng = self.engine
        if eng is None:
            return []
        try:
            raw = eng._exchange.get_balance()  # noqa: SLF001
            markets = getattr(eng._exchange, "_client", None)  # noqa: SLF001
            # Fetch active KRW markets from the underlying ccxt client
            try:
                ccxt_client = eng._exchange._exchange  # noqa: SLF001
                if not ccxt_client.markets:
                    ccxt_client.load_markets()
                active_markets = {
                    sym.split("/")[0]
                    for sym, mkt in ccxt_client.markets.items()
                    if sym.endswith("/KRW") and mkt.get("active", False)
                }
            except Exception:  # noqa: BLE001
                active_markets = None  # fallback: show all

            result = []
            for currency, bal in raw.items():
                if currency in ("info", "free", "used", "total", "KRW"):
                    continue
                if float(bal.free) <= 0:
                    continue
                if active_markets is not None and currency not in active_markets:
                    continue
                symbol = f"{currency}/KRW"
                try:
                    ticker = ccxt_client.fetch_ticker(symbol)
                    price = float(ticker.get("last") or 0)
                except Exception:  # noqa: BLE001
                    price = 0.0
                free = float(bal.free)
                eval_amount = round(free * price)
                if eval_amount < 5001:
                    continue
                result.append({
                    "currency": currency,
                    "free": free,
                    "used": float(bal.used),
                    "total": float(bal.total),
                    "price": price,
                    "eval_amount": eval_amount,
                })
            result.sort(key=lambda x: x["eval_amount"], reverse=True)
            return result
        except Exception:  # noqa: BLE001
            return []

    def pause(self) -> None:
        eng = self.engine
        if eng is not None:
            eng.pause()

    def resume(self) -> None:
        eng = self.engine
        if eng is not None:
            eng.resume()


_state = DashboardState()


def get_dashboard_state() -> DashboardState:
    """Return the module-level singleton."""
    return _state
