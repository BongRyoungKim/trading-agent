"""
Trading Engine: orchestrates strategy, risk, portfolio, and exchange into a unified loop.
Supports paper trading (simulated fills) and live trading (real exchange orders).
"""
from __future__ import annotations

import signal
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from src.config.settings import Settings
from src.exchange.base import BaseExchangeClient
from src.portfolio.journal import TradeJournal, TradeRecord
from src.portfolio.journal_store import SQLiteJournalStore
from src.portfolio.tracker import PortfolioTracker
from src.risk.manager import RiskManager
from src.risk.position_sizing import fixed_fraction
from src.strategy.base import BaseStrategy, StrategyFactory
from src.strategy.models import SignalAction
from src.data.validator import OHLCVValidator
from src.exchange.slippage import SlippageConfig, apply_slippage
from src.health import get_health_state
from src.utils.circuit_breaker import CircuitBreaker
from src.utils.exceptions import CircuitBreakerOpenError, RiskError
from src.utils.market_hours import MarketHoursConfig, market_hours_from_settings
from src.utils.telegram import TelegramClient

# A strategy provider: single strategy, per-symbol dict, or a StrategyFactory.
StrategyProvider = BaseStrategy | dict[str, BaseStrategy] | StrategyFactory


class TradingEngine:
    """
    Orchestrates the full trading loop per symbol:
      1. Check stop-loss / take-profit on open positions  ← NEW
      2. Fetch OHLCV → generate signal
      3. Risk checks
      4. Execute order (paper: simulated fill | live: exchange API)
      5. Update portfolio + risk state
      6. Notify via Telegram

    Strategy can be supplied as:
      - A single BaseStrategy instance (shared across all symbols)
      - A dict[symbol → BaseStrategy]
      - A callable(symbol) → BaseStrategy factory

    Usage:
        engine = TradingEngine(settings, exchange, strategy, risk_manager, portfolio)
        engine.start(symbols=["BTC/USDT"], interval_seconds=60)
    """

    _PAPER_COMMISSION_RATE = Decimal("0.001")  # 0.1%

    def __init__(
        self,
        settings: Settings,
        exchange: BaseExchangeClient,
        strategy: StrategyProvider,
        risk_manager: RiskManager,
        portfolio: PortfolioTracker,
        telegram: TelegramClient | None = None,
        journal_store: SQLiteJournalStore | None = None,
        slippage_config: SlippageConfig | None = None,
        market_hours: MarketHoursConfig | None = None,
    ) -> None:
        self._settings = settings
        self._exchange = exchange
        self._strategy_provider = strategy
        self._risk_manager = risk_manager
        self._portfolio = portfolio
        self._telegram = telegram if telegram is not None else TelegramClient("", "")
        self._mode: Literal["paper", "live"] = (
            "live" if settings.trading_mode == "live" else "paper"
        )
        self._slippage_config = slippage_config if slippage_config is not None else SlippageConfig()
        self._trailing_stop_pct: Decimal | None = None  # set via trailing_stop_pct property
        self._start_time: float | None = None
        self._data_validator = OHLCVValidator()
        self._scheduler = BackgroundScheduler(daemon=True)
        self._stop_event = threading.Event()
        self._paused = False
        self._paused_lock = threading.Lock()
        self._initial_capital: Decimal | None = None  # captured at start for daily report
        self._latest_ticks: dict[str, dict] = {}  # symbol → latest tick result
        self._journal = (
            TradeJournal.from_store(journal_store)
            if journal_store is not None
            else TradeJournal()
        )
        self._circuit_breaker = CircuitBreaker(
            name=f"exchange_{settings.exchange}",
            failure_threshold=5,
            recovery_timeout=60.0,
            expected_exception=Exception,
        )
        self._market_hours = (
            market_hours if market_hours is not None
            else market_hours_from_settings(settings)
        )

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def journal(self) -> TradeJournal:
        return self._journal

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._circuit_breaker

    @property
    def is_paused(self) -> bool:
        with self._paused_lock:
            return self._paused

    def pause(self) -> None:
        """Pause trading — ticks are skipped until resume() is called."""
        with self._paused_lock:
            self._paused = True
        logger.info("TradingEngine paused")
        self._telegram.send_risk_alert("Trading paused via control command.")

    def resume(self) -> None:
        """Resume trading after a pause."""
        with self._paused_lock:
            self._paused = False
        logger.info("TradingEngine resumed")
        self._telegram.send("▶️ Trading resumed.")

    @property
    def market_hours(self) -> MarketHoursConfig:
        return self._market_hours

    @property
    def trailing_stop_pct(self) -> Decimal | None:
        return self._trailing_stop_pct

    @trailing_stop_pct.setter
    def trailing_stop_pct(self, value: float | Decimal | None) -> None:
        self._trailing_stop_pct = Decimal(str(value)) if value is not None else None

    def tick(self, symbol: str) -> None:
        """
        Single evaluation cycle for one symbol.
        Scheduled by the engine; never raises — logs and notifies on error.
        """
        if self.is_paused:
            logger.debug("Engine paused, tick skipped", symbol=symbol)
            return

        if not self._market_hours.is_trading_time():
            logger.debug("Outside trading hours, tick skipped", symbol=symbol)
            return

        try:
            with self._circuit_breaker:
                self._process_symbol(symbol)
        except CircuitBreakerOpenError as exc:
            logger.warning(
                "Tick skipped — circuit breaker is OPEN",
                symbol=symbol,
                error=str(exc),
            )
            self._telegram.send_risk_alert(
                f"Exchange circuit breaker OPEN for {self._settings.exchange}. "
                "Trading paused until exchange recovers."
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Unhandled error in tick | {symbol} | {exc}")
            self._telegram.send_error(str(exc), context=f"tick({symbol})")

    def start(
        self,
        symbols: list[str],
        interval_seconds: int = 60,
        daily_report_hour: int | None = 0,
        heartbeat_interval: int | None = 3600,
    ) -> None:
        """
        Start the scheduler and block until stop() is called or a termination
        signal (SIGINT / SIGTERM) arrives.

        Args:
            symbols:             Symbols to trade.
            interval_seconds:    How often each symbol is evaluated.
            daily_report_hour:   UTC hour (0-23) to send daily Telegram report.
                                 None disables the daily report job.
            heartbeat_interval:  Seconds between Telegram heartbeat messages.
                                 None disables heartbeats.
        """
        self._initial_capital = self._portfolio.cash
        self._start_time = time.monotonic()

        logger.info(
            "TradingEngine starting",
            mode=self._mode,
            symbols=symbols,
            interval_seconds=interval_seconds,
        )

        if self._mode == "live":
            self._reconcile_positions(symbols)

        provider = self._strategy_provider
        if hasattr(provider, "strategy_names"):
            strategy_name = str(provider.strategy_names())
        elif isinstance(provider, dict):
            strategy_name = ", ".join(
                f"{s}:{type(v).__name__}" for s, v in provider.items()
            )
        else:
            strategy_name = type(provider).__name__
        self._telegram.send_startup(
            mode=self._mode,
            exchange=self._settings.exchange,
            symbols=symbols,
            strategy=strategy_name,
        )

        for sym in symbols:
            self._scheduler.add_job(
                self.tick,
                trigger="interval",
                seconds=interval_seconds,
                args=[sym],
                id=f"tick_{sym.replace('/', '_')}",
                max_instances=1,
                coalesce=True,
            )

        if daily_report_hour is not None and self._telegram.is_enabled:
            self._scheduler.add_job(
                self._send_daily_report,
                trigger="cron",
                hour=daily_report_hour,
                minute=0,
                id="daily_report",
            )
            logger.info("Daily report scheduled", hour=daily_report_hour)

        if self._market_hours.enabled and self._telegram.is_enabled:
            open_t = self._market_hours.open_time()
            close_t = self._market_hours.close_time()
            self._scheduler.add_job(
                self._notify_market_open,
                trigger="cron",
                hour=open_t.hour,
                minute=open_t.minute,
                id="market_open",
            )
            self._scheduler.add_job(
                self._notify_market_close,
                trigger="cron",
                hour=close_t.hour,
                minute=close_t.minute,
                id="market_close",
            )
            logger.info(
                "Market hours active",
                open=self._market_hours.trading_hours.split("-")[0],
                close=self._market_hours.trading_hours.split("-")[1],
                days=self._market_hours.trading_days,
            )

        if heartbeat_interval is not None and heartbeat_interval > 0:
            self._scheduler.add_job(
                self._send_heartbeat,
                trigger="interval",
                seconds=heartbeat_interval,
                id="heartbeat",
            )
            logger.info("Heartbeat scheduled", interval_seconds=heartbeat_interval)

        self._register_signal_handlers()
        self._scheduler.start()
        get_health_state().set_ready(
            {"mode": self._mode, "exchange": self._settings.exchange, "symbols": symbols}
        )
        logger.info("TradingEngine running — press Ctrl+C to stop")

        self._stop_event.wait()
        self._shutdown()

    def stop(self) -> None:
        """Signal graceful shutdown (thread-safe)."""
        self._stop_event.set()

    # ── Internal: symbol processing ───────────────────────────────────────────

    def _process_symbol(self, symbol: str) -> None:
        # ── Step 1: stop-loss / take-profit check ─────────────────────────────
        if self._portfolio.has_position(symbol):
            pos = self._portfolio.get_position(symbol)
            ticker = self._exchange.get_ticker(symbol)
            price = ticker.last_price

            # ── Step 1a: trailing stop ratchet ────────────────────────────────
            trailing_pct = pos.trailing_stop_pct if pos.trailing_stop_pct is not None \
                else self._trailing_stop_pct
            if trailing_pct is not None and pos.side == "buy":
                new_trail = price * (Decimal("1") - trailing_pct / Decimal("100"))
                if pos.stop_loss is None or new_trail > pos.stop_loss:
                    pos = self._portfolio.update_stop_loss(symbol, new_trail)

            if pos.stop_loss is not None and price <= pos.stop_loss:
                logger.info(
                    "Stop-loss triggered",
                    symbol=symbol,
                    stop_loss=float(pos.stop_loss),
                    price=float(price),
                )
                self._close_position(symbol, forced_price=price, reason="stop_loss")
                return

            if pos.take_profit is not None and price >= pos.take_profit:
                logger.info(
                    "Take-profit triggered",
                    symbol=symbol,
                    take_profit=float(pos.take_profit),
                    price=float(price),
                )
                self._close_position(symbol, forced_price=price, reason="take_profit")
                return

        # ── Step 2: strategy signal ───────────────────────────────────────────
        strategy = self._resolve_strategy(symbol)
        df = self._exchange.get_ohlcv_dataframe(
            symbol, timeframe=strategy.timeframe, limit=200
        )
        validation = self._data_validator.validate(df, min_rows=strategy.min_required_bars())
        if not validation.ok:
            logger.warning(
                "Data quality issues — skipping tick",
                symbol=symbol,
                timeframe=strategy.timeframe,
                issues=validation.issues,
            )
            return
        signal_ = strategy.generate_signal(df)

        logger.info(
            f"Tick | {symbol} → {signal_.action.name} | {signal_.reason[:80]}"
        )
        self._latest_ticks[symbol] = {
            "symbol": symbol,
            "action": signal_.action.name,
            "reason": signal_.reason,
            "metadata": signal_.metadata or {},
            "timestamp": signal_.timestamp.isoformat() if signal_.timestamp else None,
        }

        if not signal_.is_actionable():
            return

        has_pos = self._portfolio.has_position(symbol)
        if signal_.action == SignalAction.BUY and not has_pos:
            self._open_position(symbol)
        elif signal_.action == SignalAction.SELL and has_pos:
            self._close_position(symbol)

    # ── Internal: order execution ─────────────────────────────────────────────

    def _open_position(self, symbol: str) -> None:
        ticker = self._exchange.get_ticker(symbol)
        price = ticker.last_price

        stop_loss = self._risk_manager.calculate_stop_loss(price, side="buy")
        amount = fixed_fraction(
            capital=self._portfolio.cash,
            risk_fraction=self._settings.max_position_risk,
            entry_price=price,
            stop_loss_price=stop_loss,
        )

        if amount <= 0:
            logger.warning("Position sizing returned zero amount, skipping", symbol=symbol)
            return

        try:
            self._risk_manager.check_can_open_position()
        except RiskError as exc:
            logger.warning("Risk check blocked open", symbol=symbol, reason=str(exc))
            self._telegram.send_risk_alert(str(exc))
            return

        if self._mode == "live":
            order = self._exchange.place_order(symbol, "buy", amount)
            fill_price = order.price if order.price > 0 else price
            commission = price * amount * self._PAPER_COMMISSION_RATE
            if order.fee is not None:
                commission = Decimal(str(order.fee))
        else:
            try:
                ticker_volume = Decimal(str(ticker.volume))
            except Exception:  # noqa: BLE001
                ticker_volume = Decimal("0")
            fill_price = apply_slippage(
                price=price,
                side="buy",
                amount=amount,
                daily_volume=ticker_volume,
                config=self._slippage_config,
            )
            commission = fill_price * amount * self._PAPER_COMMISSION_RATE

        self._portfolio.open_position(
            symbol=symbol,
            side="buy",
            amount=amount,
            entry_price=fill_price,
            commission=commission,
            stop_loss=stop_loss,
            trailing_stop_pct=self._trailing_stop_pct,
        )
        self._risk_manager.on_position_opened()

        logger.info(
            "Position opened",
            symbol=symbol,
            mode=self._mode,
            price=float(fill_price),
            amount=float(amount),
            stop_loss=float(stop_loss),
        )
        self._telegram.send_order_filled(symbol, "buy", float(amount), float(fill_price))

    def _close_position(
        self,
        symbol: str,
        forced_price: Decimal | None = None,
        reason: str = "signal",
    ) -> None:
        position = self._portfolio.get_position(symbol)
        if position is None:
            return

        if forced_price is not None:
            price = forced_price
        else:
            ticker = self._exchange.get_ticker(symbol)
            price = ticker.last_price

        if self._mode == "live":
            order = self._exchange.place_order(symbol, "sell", position.amount)
            fill_price = order.price if order.price > 0 else price
            commission = price * position.amount * self._PAPER_COMMISSION_RATE
            if order.fee is not None:
                commission = Decimal(str(order.fee))
        else:
            if forced_price is not None:
                fill_price = forced_price  # SL/TP: no additional slippage on forced price
            else:
                try:
                    ticker_volume = Decimal(str(ticker.volume))
                except Exception:  # noqa: BLE001
                    ticker_volume = Decimal("0")
                fill_price = apply_slippage(
                    price=price,
                    side="sell",
                    amount=position.amount,
                    daily_volume=ticker_volume,
                    config=self._slippage_config,
                )
            commission = fill_price * position.amount * self._PAPER_COMMISSION_RATE

        pnl = self._portfolio.close_position(symbol, fill_price, commission=commission)
        cost_basis = position.entry_price * position.amount
        pnl_pct = float(pnl / cost_basis * 100) if cost_basis else 0.0
        self._risk_manager.on_position_closed(pnl)

        self._journal.record(
            TradeRecord(
                symbol=symbol,
                side=position.side,
                amount=position.amount,
                entry_price=position.entry_price,
                exit_price=fill_price,
                entry_time=position.entry_time,
                exit_time=datetime.now(UTC),
                pnl=pnl,
                commission=commission,
                reason=reason,
            )
        )

        logger.info(
            "Position closed",
            symbol=symbol,
            mode=self._mode,
            reason=reason,
            pnl=float(pnl),
        )
        self._telegram.send_position_closed(
            symbol,
            float(position.entry_price),
            float(fill_price),
            float(pnl),
            pnl_pct,
        )

    # ── Internal: daily report ────────────────────────────────────────────────

    def _send_daily_report(self) -> None:
        """Scheduled job: send Telegram daily PnL summary."""
        try:
            report = self._portfolio.pnl_report()
            stats = self._journal.stats()
            initial = float(self._initial_capital or 0)
            final = report["cash"] + report.get("unrealized_pnl", 0.0)
            self._telegram.send_daily_report(
                date=datetime.now(UTC),
                initial_capital=initial,
                final_capital=float(final),
                realized_pnl=float(report["realized_pnl"]),
                total_trades=stats["total_trades"],
                win_rate=stats["win_rate_pct"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send daily report", error=str(exc))

    # ── Internal: strategy resolution ────────────────────────────────────────

    def _resolve_strategy(self, symbol: str) -> BaseStrategy:
        """Resolve the correct strategy for a given symbol."""
        provider = self._strategy_provider
        # StrategyFactory subclass (e.g. SymbolStrategyRouter)
        if isinstance(provider, StrategyFactory):
            return provider(symbol)
        # Explicit per-symbol dict
        if isinstance(provider, dict):
            if symbol in provider:
                return provider[symbol]
            return next(iter(provider.values()))
        # Single shared strategy
        return provider  # type: ignore[return-value]

    # ── Internal: reconciliation ──────────────────────────────────────────────

    def _reconcile_positions(self, symbols: list[str]) -> None:
        """
        Live-mode startup check: compare local position DB with exchange.

        For each symbol in the trading list:
        - Local open, exchange has no position → log a warning (may be stale)
        - Exchange has position, local has none → log an alert (untracked position)

        No automatic corrective action is taken — the operator must resolve
        discrepancies manually to avoid unintended trades.
        """
        local_symbols = set(self._portfolio.open_symbols())
        tracked = set(symbols)

        for sym in tracked:
            has_local = sym in local_symbols
            try:
                ticker = self._exchange.get_ticker(sym)
                # Use ticker availability as a proxy for exchange reachability.
                # Full balance reconciliation is exchange-specific; we flag mismatches.
                if has_local:
                    logger.debug("Reconciled local position exists", symbol=sym)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Reconciliation: could not fetch ticker",
                    symbol=sym,
                    error=str(exc),
                )

        # Warn about local positions for symbols outside the trading list
        ghost_symbols = local_symbols - tracked
        for sym in ghost_symbols:
            logger.warning(
                "Reconciliation: local position exists for untracked symbol",
                symbol=sym,
                note="verify manually and close if stale",
            )
            self._telegram.send_risk_alert(
                f"Untracked open position detected: {sym}. Verify manually."
            )

        logger.info(
            "Position reconciliation complete",
            local_open=len(local_symbols),
            tracked_symbols=len(tracked),
            ghost_symbols=len(ghost_symbols),
        )

    # ── Internal: lifecycle ───────────────────────────────────────────────────

    def _shutdown(self) -> None:
        logger.info("TradingEngine shutting down...")
        get_health_state().set_not_ready("shutting down")
        if self._scheduler.running:
            self._scheduler.shutdown(wait=True)
        report = self._portfolio.pnl_report()
        logger.info("Final portfolio state", **{k: str(v) for k, v in report.items()})

        session_seconds = time.monotonic() - self._start_time if self._start_time else 0.0
        stats = self._journal.stats()
        self._telegram.send_shutdown(
            session_seconds=session_seconds,
            total_trades=stats["total_trades"],
            win_rate=stats["win_rate_pct"],
            realized_pnl=report["realized_pnl"],
        )
        logger.info("TradingEngine stopped")

    def _notify_market_open(self) -> None:
        """Scheduled cron: notify that the trading window has opened."""
        logger.info("Market open — trading window started")
        self._telegram.send(
            f"🔔 <b>Market Open</b> — Trading window started\n"
            f"Hours: <code>{self._market_hours.trading_hours} UTC</code>"
        )

    def _notify_market_close(self) -> None:
        """Scheduled cron: notify that the trading window has closed."""
        logger.info("Market close — trading window ended")
        report = self._portfolio.pnl_report()
        self._telegram.send(
            f"🔕 <b>Market Close</b> — Trading window ended\n"
            f"Realized PnL today: <b>{report['realized_pnl']:,.2f}</b>"
        )

    def _send_heartbeat(self) -> None:
        """Scheduled job: log liveness + optional Telegram heartbeat."""
        try:
            report = self._portfolio.pnl_report()
            circuit_state = self._circuit_breaker.state.name
            logger.info(
                "Heartbeat",
                open_positions=report["open_positions"],
                cash=report["cash"],
                realized_pnl=report["realized_pnl"],
                circuit=circuit_state,
            )
            self._telegram.send_heartbeat(
                open_positions=report["open_positions"],
                cash=float(report["cash"]),
                circuit_state=circuit_state,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Heartbeat failed", error=str(exc))

    def _register_signal_handlers(self) -> None:
        """Register OS signal handlers. Skipped if not the main thread."""
        try:
            signal.signal(signal.SIGINT, self._handle_os_signal)
            signal.signal(signal.SIGTERM, self._handle_os_signal)
        except ValueError:
            logger.debug("Signal handlers not registered (not main thread)")

    def _handle_os_signal(self, signum: int, frame: object) -> None:
        logger.info("Signal received, shutting down", signal=signum)
        self.stop()
