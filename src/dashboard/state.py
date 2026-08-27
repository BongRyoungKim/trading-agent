"""
DashboardState: thread-safe bridge between TradingEngine and the web dashboard.

The engine registers itself here at startup. FastAPI route handlers read state
through this singleton — keeping the web layer decoupled from the engine.
"""
from __future__ import annotations

import math
import queue
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.engine import TradingEngine


class DashboardState:
    """Singleton that holds an optional reference to the running TradingEngine."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engine: TradingEngine | None = None
        self._tick_queues: list[queue.Queue] = []
        self._tick_queues_lock = threading.Lock()

    def register_engine(self, engine: TradingEngine) -> None:
        with self._lock:
            self._engine = engine
        engine.register_tick_callback(self.notify_tick)

    def notify_tick(self, tick: dict) -> None:
        """Called by the engine after each tick evaluation; pushes to SSE subscribers."""
        eng = self.engine
        if eng is not None:
            ranked = getattr(eng, "_ranked_symbols", [])
            rank_map = {sym: i for i, sym in enumerate(ranked)}
            tick = {**tick, "_rank": rank_map.get(tick.get("symbol", ""), len(ranked))}
        with self._tick_queues_lock:
            queues = list(self._tick_queues)
        for q in queues:
            try:
                q.put_nowait(tick)
            except Exception:  # noqa: BLE001
                pass

    def subscribe_ticks(self) -> queue.Queue:
        """Register a new SSE subscriber; returns a queue that receives tick dicts."""
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._tick_queues_lock:
            self._tick_queues.append(q)
        return q

    def unsubscribe_ticks(self, q: queue.Queue) -> None:
        """Remove an SSE subscriber queue."""
        with self._tick_queues_lock:
            try:
                self._tick_queues.remove(q)
            except ValueError:
                pass

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
        positions = []
        latest_ticks: dict = getattr(eng, "_latest_ticks", {})  # noqa: SLF001
        for sym in eng._portfolio.open_symbols():  # noqa: SLF001
            pos = eng._portfolio.get_position(sym)
            if pos is None:
                continue
            # Use price from latest tick cache (already fetched by engine; avoids rate-limit hits).
            # Fall back to entry_price if tick not yet available.
            tick = latest_ticks.get(sym, {})
            tick_price = (tick.get("metadata") or {}).get("price")
            try:
                current_price = float(tick_price) if tick_price is not None else float(pos.entry_price)
            except Exception:  # noqa: BLE001
                current_price = float(pos.entry_price)
            entry = float(pos.entry_price)
            amount = float(pos.amount)
            unrealized_pnl = (current_price - entry) * amount
            pnl_pct = (current_price - entry) / entry * 100 if entry else 0.0
            positions.append({
                "symbol": sym,
                "side": pos.side,
                "amount": amount,
                "entry_price": entry,
                "current_price": current_price,
                "unrealized_pnl": round(unrealized_pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "entry_time": pos.entry_time.isoformat(),
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
        """Return journal statistics (win rate, profit factor, etc.).

        journal.stats()의 profit_factor는 손실 거래가 하나도 없으면
        수학적으로 float("inf")가 된다. 이 값을 그대로 JSONResponse에
        태우면 Starlette가 allow_nan=False로 직렬화하다가
        "Out of range float values are not JSON compliant" 500 에러를
        던지므로(/api/stats), 여기서 None으로 치환해 API/템플릿 양쪽에서
        "무한대"를 나타내는 공통 값으로 쓴다.
        """
        eng = self.engine
        if eng is None:
            return {}
        stats = dict(eng.journal.stats())  # noqa: SLF001
        pf = stats.get("profit_factor")
        if pf is not None and not math.isfinite(pf):
            stats["profit_factor"] = None
        return stats

    def get_equity_curve(self) -> list[dict]:
        """Return daily PnL data points for charting (one entry per day)."""
        eng = self.engine
        if eng is None:
            return []
        trades = eng.journal.trades  # noqa: SLF001
        daily: dict[str, float] = {}
        for t in trades:
            date = t.exit_time.strftime("%Y-%m-%d")
            daily[date] = daily.get(date, 0.0) + float(t.pnl)
        return [
            {"time": date, "pnl": round(pnl, 4)}
            for date, pnl in sorted(daily.items())
        ]

    def get_strategy_info(self) -> dict:
        """Return strategy name, parameters, and signal criteria for the dashboard."""
        eng = self.engine
        if eng is None:
            return {}
        # Use first tracked symbol to resolve strategy params
        symbols = list(getattr(eng, "_latest_ticks", {}).keys())
        if not symbols:
            # Fall back to any symbol in the scheduler
            try:
                jobs = eng._scheduler.get_jobs()  # noqa: SLF001
                symbols = [j.id.replace("tick_", "") for j in jobs if j.id.startswith("tick_")]
            except Exception:  # noqa: BLE001
                return {}
        if not symbols:
            return {}
        try:
            strategy = eng._resolve_strategy(symbols[0])  # noqa: SLF001
            params = strategy.get_parameters() if hasattr(strategy, "get_parameters") else {}
            # Remove the per-symbol key to show generic params once
            params.pop("symbol", None)
            return {
                "name": type(strategy).__name__,
                "timeframe": strategy.timeframe,
                "parameters": params,
            }
        except Exception:  # noqa: BLE001
            return {}

    def get_ticks(self) -> list[dict]:
        """Return latest tick evaluation result per symbol, ordered by 24h ranked list (Upbit quoteVolume).
        Each tick includes a _rank field so the client can maintain the same order after SSE updates.
        """
        eng = self.engine
        if eng is None:
            return []
        ticks = getattr(eng, "_latest_ticks", {})
        ranked = getattr(eng, "_ranked_symbols", [])
        rank_map = {sym: i for i, sym in enumerate(ranked)}
        sorted_ticks = sorted(
            ticks.values(),
            key=lambda x: rank_map.get(x.get("symbol", ""), len(ranked)),
        )
        return [
            {**t, "_rank": rank_map.get(t.get("symbol", ""), len(ranked))}
            for t in sorted_ticks
        ]

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
                avg_buy_price = float(bal.avg_buy_price)
                result.append({
                    "currency": currency,
                    "free": free,
                    "used": float(bal.used),
                    "total": float(bal.total),
                    "price": price,
                    "eval_amount": eval_amount,
                    "avg_buy_price": avg_buy_price,
                    "buy_amount": round(float(bal.total) * avg_buy_price),
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
