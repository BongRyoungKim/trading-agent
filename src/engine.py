"""
Trading Engine: orchestrates strategy, risk, portfolio, and exchange into a unified loop.
Supports paper trading (simulated fills) and live trading (real exchange orders).
"""
from __future__ import annotations

import signal
import threading
import time
from datetime import UTC, datetime, timedelta
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
from src.utils.exceptions import CircuitBreakerOpenError, ConnectionError, InsufficientFundsError, PositionLimitExceededError, RiskError
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
        # SL/TP risk knobs — overridable via properties (CLI / live_params auto-tuning)
        self._sl_floor_pct: Decimal = Decimal("3.0")     # max SL distance from entry, %
        self._sl_ceiling_pct: Decimal = Decimal("1.5")   # min SL distance from entry, %
        self._atr_multiplier: float = 2.0
        self._tp_rr_multiplier: Decimal = Decimal("1.5")
        self._start_time: float | None = None
        self._data_validator = OHLCVValidator()
        self._scheduler = BackgroundScheduler(daemon=True)
        self._stop_event = threading.Event()
        self._paused = False
        self._paused_lock = threading.Lock()
        self._buy_lock = threading.Lock()  # prevents simultaneous BUY races across symbols
        self._initial_capital: Decimal | None = None  # captured at start for daily report
        self._latest_ticks: dict[str, dict] = {}  # symbol → latest tick result
        self._tick_callbacks: list = []  # called with tick dict after each evaluation
        self._sync_anchor: str = ""  # first symbol in the list — triggers KRW sync
        self._tick_symbols: set[str] = set()  # currently scheduled tick symbols
        self._pinned_symbols: frozenset[str] = frozenset()  # user-specified symbols; if non-empty, auto-refresh cannot add/remove them
        self._tick_interval: int = 60  # stored at start() for dynamic symbol additions
        self._top_n_symbols: int = 20  # how many symbols _refresh_symbols() fetches by volume; set via start()
        self._ranked_symbols: list[str] = []  # 24h vol-ranked order from last refresh
        self._symbol_blacklist: frozenset[str] = frozenset()  # symbols never traded
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

    def register_tick_callback(self, fn) -> None:
        """Register a callable(tick_dict) to be called after each symbol evaluation."""
        self._tick_callbacks.append(fn)

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

    @property
    def sl_floor_pct(self) -> Decimal:
        return self._sl_floor_pct

    @sl_floor_pct.setter
    def sl_floor_pct(self, value: float | Decimal) -> None:
        self._sl_floor_pct = Decimal(str(value))

    @property
    def sl_ceiling_pct(self) -> Decimal:
        return self._sl_ceiling_pct

    @sl_ceiling_pct.setter
    def sl_ceiling_pct(self, value: float | Decimal) -> None:
        self._sl_ceiling_pct = Decimal(str(value))

    @property
    def atr_multiplier(self) -> float:
        return self._atr_multiplier

    @atr_multiplier.setter
    def atr_multiplier(self, value: float) -> None:
        self._atr_multiplier = float(value)

    @property
    def tp_rr_multiplier(self) -> Decimal:
        return self._tp_rr_multiplier

    @tp_rr_multiplier.setter
    def tp_rr_multiplier(self, value: float | Decimal) -> None:
        self._tp_rr_multiplier = Decimal(str(value))

    @property
    def symbol_blacklist(self) -> frozenset[str]:
        return self._symbol_blacklist

    @symbol_blacklist.setter
    def symbol_blacklist(self, symbols: list[str] | set[str] | frozenset[str]) -> None:
        self._symbol_blacklist = frozenset(symbols)
        logger.info("Symbol blacklist updated", blacklist=sorted(self._symbol_blacklist))

    def _check_exit_conditions(self, symbol: str) -> bool:
        """
        Check and execute SL/TP/trailing/time-stop/dust removal for an open position.
        Returns True if the position was closed, False otherwise.
        Called before the market-hours guard so positions are protected 24/7.
        """
        if not self._portfolio.has_position(symbol):
            return False

        pos = self._portfolio.get_position(symbol)
        ticker = self._exchange.get_ticker(symbol)
        price = ticker.last

        # Propagate live ticker price to _latest_ticks immediately so the
        # dashboard reflects the current price between strategy evaluations.
        existing_tick = self._latest_ticks.get(symbol)
        if existing_tick is not None:
            updated_meta = {**(existing_tick.get("metadata") or {}), "price": float(price)}
            self._latest_ticks[symbol] = {**existing_tick, "metadata": updated_meta}

        # ── dust position removal (value ≤ 5,001 KRW) ─────────────────────
        if pos.amount * price <= self._DUST_THRESHOLD_KRW:
            pnl = self._portfolio.close_position(symbol, price, commission=Decimal("0"))
            self._risk_manager.on_position_closed(pnl)
            logger.info(
                "Dust position removed",
                symbol=symbol,
                value_krw=float(pos.amount * price),
                pnl=float(pnl),
            )
            self._journal.record(
                TradeRecord(
                    symbol=symbol,
                    side=pos.side,
                    amount=pos.amount,
                    entry_price=pos.entry_price,
                    exit_price=price,
                    entry_time=pos.entry_time,
                    exit_time=datetime.now(UTC),
                    pnl=pnl,
                    commission=Decimal("0"),
                    reason="dust",
                )
            )
            return True

        # ── trailing stop ratchet ──────────────────────────────────────────
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
            self._notify_close(symbol, price, reason="stop_loss")
            return True

        if pos.take_profit is not None and price >= pos.take_profit:
            logger.info(
                "Take-profit triggered",
                symbol=symbol,
                take_profit=float(pos.take_profit),
                price=float(price),
            )
            self._close_position(symbol, forced_price=price, reason="take_profit")
            self._notify_close(symbol, price, reason="take_profit")
            return True

        # ── time-based stop — cut losers after 60 min at -0.5% ────────────
        # Widened from 45 min / -0.3% to match the wider 1.5% SL regime.
        hold_min = (datetime.now(UTC) - pos.entry_time).total_seconds() / 60
        if hold_min >= 60 and price < pos.entry_price * Decimal("0.995"):
            logger.info(
                "Time-stop triggered — position losing after 45 min",
                symbol=symbol,
                hold_min=round(hold_min),
                entry_price=float(pos.entry_price),
                price=float(price),
                loss_pct=round(float((price - pos.entry_price) / pos.entry_price * 100), 2),
            )
            self._close_position(symbol, forced_price=price, reason="time_stop")
            self._notify_close(symbol, price, reason="time_stop")
            return True

        return False

    def tick(self, symbol: str) -> None:
        """
        Single evaluation cycle for one symbol.
        Scheduled by the engine; never raises — logs and notifies on error.
        """
        if self.is_paused:
            logger.debug("Engine paused, tick skipped", symbol=symbol)
            return

        # SL/TP/trailing/time-stop must fire even outside trading hours to
        # protect open positions around the clock.
        if not self._market_hours.is_trading_time():
            if self._portfolio.has_position(symbol):
                try:
                    with self._circuit_breaker:
                        self._check_exit_conditions(symbol)
                except Exception:  # noqa: BLE001
                    pass
            else:
                logger.debug("Outside trading hours, tick skipped", symbol=symbol)
            return

        # ── Live: sync portfolio cash with real KRW balance once per cycle ───
        if self._mode == "live" and symbol == self._sync_anchor:
            try:
                bal = self._exchange.get_balance()
                krw = bal.get("KRW")
                if krw is not None:
                    self._portfolio.sync_cash(Decimal(str(krw.free)))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"KRW balance sync failed: {exc}")

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
        except ConnectionError as exc:
            # Transient network error — retry decorator already attempted 3×.
            # Log as warning only; no Telegram alert to avoid spam.
            logger.warning(
                "Tick skipped — exchange connection error (all retries exhausted)",
                symbol=symbol,
                error=str(exc),
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
        pin_symbols: bool = True,
        weekly_report_day: str | None = "mon",
        weekly_report_hour: int = 9,
        top_n_symbols: int = 20,
        news_tuning_hour: int | None = None,
        news_tuning_dry_run: bool = True,
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
            pin_symbols:         If False, auto-refresh can freely add/remove symbols.
                                 Use with --top-symbols for dynamic universe tracking.
            weekly_report_day:  APScheduler cron day_of_week for the weekly Telegram
                                 digest (e.g. "mon"). None disables the weekly report.
            weekly_report_hour: Hour (0-23) to send the weekly report.
            top_n_symbols:      How many symbols _refresh_symbols() fetches by 24h
                                 volume on each auto-refresh. Mirrors --top-symbols.
            news_tuning_hour:   KST hour (0-23) to run the daily news-sentiment
                                 auto-tuner. None (default) disables it entirely —
                                 opt-in only.
            news_tuning_dry_run: If True (default), the news tuner only computes
                                 and reports what it would change without applying
                                 it. Set False once you've watched it run for a
                                 while and trust the adjustments.
        """
        self._initial_capital = self._portfolio.cash
        self._start_time = time.monotonic()
        self._tick_interval = interval_seconds
        self._top_n_symbols = top_n_symbols if top_n_symbols > 0 else 20
        self._news_tuning_dry_run = news_tuning_dry_run
        self._tick_symbols = set(symbols)
        if pin_symbols:
            self._pinned_symbols = frozenset(symbols)
        # else: _pinned_symbols stays frozenset() — auto-refresh manages universe freely
        self._sync_anchor = symbols[0] if symbols else ""

        logger.info(
            "TradingEngine starting",
            mode=self._mode,
            symbols=symbols,
            interval_seconds=interval_seconds,
        )

        if self._mode == "live":
            self._reconcile_positions(symbols)
            # Any position restored from DB or exchange that isn't in the initial
            # symbol list must still be scheduled for ticking (SL/TP, exit signals).
            extra = [s for s in self._portfolio.open_symbols() if s not in set(symbols)]
            if extra:
                logger.info("Scheduling tick jobs for restored positions outside initial symbol list", symbols=extra)
                symbols = symbols + extra

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

        # 심볼별 tick 작업을 모두 같은 순간에 등록하면 APScheduler가 매 사이클마다
        # N개 심볼의 API 호출(get_ohlcv 등)을 거의 동시에(같은 1~2초 안에) 실행해
        # 거래소 초당 rate limit을 넘겨 "Retryable error, backing off"(429)가
        # 반복됐다. interval_seconds를 심볼 수만큼 균등 분산해 각 심볼의 최초
        # 실행 시각을 stagger_seconds씩 어긋나게 잡아 호출을 고르게 퍼뜨린다.
        stagger_seconds = interval_seconds / len(symbols) if symbols else 0.0
        now = datetime.now(UTC)
        for i, sym in enumerate(symbols):
            self._scheduler.add_job(
                self.tick,
                trigger="interval",
                seconds=interval_seconds,
                next_run_time=now + timedelta(seconds=i * stagger_seconds),
                args=[sym],
                id=f"tick_{sym.replace('/', '_')}",
                max_instances=1,
                coalesce=True,
            )

        # Dynamic top-N symbol refresh (Upbit only)
        if hasattr(self._exchange, "get_top_symbols_by_volume"):
            self._scheduler.add_job(
                self._refresh_symbols,
                trigger="interval",
                minutes=10,
                id="symbol_refresh",
            )
            logger.info("Symbol auto-refresh scheduled (every 10 min)")
            # Run once immediately after scheduler starts (deferred via first interval)
            self._scheduler.add_job(
                self._refresh_symbols,
                trigger="date",
                id="symbol_refresh_init",
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

        if weekly_report_day and self._telegram.is_enabled:
            self._scheduler.add_job(
                self._send_weekly_report,
                trigger="cron",
                day_of_week=weekly_report_day,
                hour=weekly_report_hour,
                minute=0,
                id="weekly_report",
            )
            logger.info(
                "Weekly report scheduled", day=weekly_report_day, hour=weekly_report_hour
            )

        if news_tuning_hour is not None:
            self._scheduler.add_job(
                self._run_news_tuning,
                trigger="cron",
                hour=news_tuning_hour,
                minute=0,
                id="news_tuning",
            )
            logger.info(
                "Daily news-sentiment tuning scheduled",
                hour=news_tuning_hour,
                dry_run=news_tuning_dry_run,
            )

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

        # Auto-tuning restart watch — checked independently of the daily report
        # cron so a parameter change applied by ScheduledTaskRunner takes effect
        # within ~1 minute instead of waiting up to 24h for the next daily run.
        self._scheduler.add_job(
            self._check_pending_restart,
            trigger="interval",
            seconds=60,
            id="restart_watch",
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

    _DUST_THRESHOLD_KRW = Decimal("5001")

    def _process_symbol(self, symbol: str) -> None:
        # ── Step 1: stop-loss / take-profit check ─────────────────────────────
        if self._check_exit_conditions(symbol):
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
        tick_data = {
            "symbol": symbol,
            "action": signal_.action.name,
            "reason": signal_.reason,
            "metadata": signal_.metadata or {},
            "timestamp": signal_.timestamp.isoformat() if signal_.timestamp else None,
        }
        self._latest_ticks[symbol] = tick_data
        for _cb in self._tick_callbacks:
            try:
                _cb(tick_data)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Tick callback error (non-fatal): {exc}")

        if not signal_.is_actionable():
            return

        has_pos = self._portfolio.has_position(symbol)
        if signal_.action == SignalAction.BUY and not has_pos:
            if symbol in self._symbol_blacklist:
                logger.debug("Symbol blacklisted — buy skipped", symbol=symbol)
                return
            if self._portfolio.cash < Decimal("10000"):
                logger.info(
                    "Cash < 10,000 KRW — buy cancelled until next evaluation",
                    symbol=symbol,
                    cash=float(self._portfolio.cash),
                )
                return
            # Acquire lock to prevent simultaneous buys across parallel symbol threads
            if not self._buy_lock.acquire(blocking=False):
                logger.debug("Buy lock busy — skipping concurrent open", symbol=symbol)
                return
            try:
                # Re-check cash inside lock (another thread may have just spent it)
                if self._portfolio.cash < Decimal("10000"):
                    return
                self._open_position(symbol, signal_)
            finally:
                self._buy_lock.release()
        elif signal_.action == SignalAction.SELL and has_pos:
            pos = self._portfolio.get_position(symbol)
            hold_seconds = (datetime.now(UTC) - pos.entry_time).total_seconds()
            if hold_seconds < 1800:  # 30 min minimum hold
                logger.debug(
                    "Signal SELL suppressed — min hold time not reached",
                    symbol=symbol,
                    hold_seconds=int(hold_seconds),
                )
                return
            # Only exit on signal if unrealized gain covers commissions (>= 0.3%)
            current_price = signal_.metadata.get("price") if signal_.metadata else None
            if current_price is not None:
                unrealized_pct = (float(current_price) - float(pos.entry_price)) / float(pos.entry_price) * 100
                if unrealized_pct < 0.3:
                    logger.debug(
                        "Signal SELL suppressed — below min profit threshold",
                        symbol=symbol,
                        unrealized_pct=round(unrealized_pct, 2),
                    )
                    return
            self._close_position(symbol)

    # ── Internal: order execution ─────────────────────────────────────────────

    def _open_position(self, symbol: str, signal_: object | None = None) -> None:
        ticker = self._exchange.get_ticker(symbol)
        price = ticker.last

        # ATR-based SL, clamped to [sl_ceiling_pct, sl_floor_pct]% below entry.
        # Dual clamp:
        #   sl_ceiling_pct (min distance): prevents noise stops on low-ATR coins like BTC
        #   sl_floor_pct   (max distance): caps max loss per trade
        # Both knobs and atr_multiplier/tp_rr_multiplier are live-tunable (see
        # src.config.live_params) — auto-adjusted by ScheduledTaskRunner and
        # picked up on the next process restart.
        atr_val = signal_.metadata.get("atr") if signal_ and signal_.metadata else None
        stop_loss = self._risk_manager.calculate_stop_loss(
            price, side="buy",
            atr_value=float(atr_val) if atr_val else None,
            atr_multiplier=self._atr_multiplier,
        )
        sl_ceiling = price * (Decimal("1") - self._sl_ceiling_pct / Decimal("100"))
        sl_floor   = price * (Decimal("1") - self._sl_floor_pct / Decimal("100"))
        if stop_loss > sl_ceiling:
            stop_loss = sl_ceiling
        if stop_loss < sl_floor:
            stop_loss = sl_floor
        # Take profit: tp_rr_multiplier × SL distance.
        # Trailing stop locks profits once price moves toward TP.
        sl_distance = price - stop_loss
        tp_distance = sl_distance * self._tp_rr_multiplier
        take_profit = price + tp_distance

        amount = fixed_fraction(
            capital=self._portfolio.cash,
            risk_fraction=self._settings.max_position_risk,
            entry_price=price,
            stop_loss_price=stop_loss,
        )

        if amount <= 0:
            logger.warning("Position sizing returned zero amount, skipping", symbol=symbol)
            return

        # ── Cap notional at 99% of available cash (leave 1% for fees/rounding) ─
        max_notional = self._portfolio.cash * Decimal("0.99")
        max_amount = max_notional / price
        if amount > max_amount:
            amount = max_amount

        # ── Minimum order size: 10,000 KRW ───────────────────────────────────
        _MIN_KRW = Decimal("10000")
        min_amount = _MIN_KRW / price
        if amount < min_amount:
            logger.info(
                "Amount below minimum — bumping to 10,000 KRW",
                symbol=symbol,
                original=float(amount),
                adjusted=float(min_amount),
            )
            amount = min_amount

        try:
            self._risk_manager.check_can_open_position()
        except PositionLimitExceededError:
            # Normal operating behaviour — max 1 concurrent position is active.
            # Log at DEBUG only; no Telegram alert (not an error condition).
            logger.debug(
                "Position limit active — buy skipped",
                symbol=symbol,
                open_positions=self._risk_manager._state.open_positions,  # noqa: SLF001
            )
            return
        except RiskError as exc:
            logger.warning(f"Risk check blocked open: {exc}", symbol=symbol)
            self._telegram.send_risk_alert(str(exc))
            return

        if self._mode == "live":
            try:
                order = self._exchange.place_order(symbol, "buy", amount)
            except Exception as exc:  # noqa: BLE001
                # Sync cash with actual exchange balance and abort
                logger.warning(
                    "Buy order failed — syncing cash and skipping",
                    symbol=symbol,
                    amount=float(amount),
                    error=str(exc),
                )
                try:
                    bal = self._exchange.get_balance()
                    krw = bal.get("KRW")
                    if krw is not None:
                        self._portfolio.sync_cash(Decimal(str(krw.free)))
                except Exception:  # noqa: BLE001
                    pass
                return
            fill_price = order.price if order.price > 0 else price
            commission = fill_price * amount * self._PAPER_COMMISSION_RATE
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
            take_profit=take_profit,
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

    def _notify_close(self, symbol: str, price: Decimal, reason: str) -> None:
        """Fire tick_callbacks with a synthetic SELL tick after SL/TP/time-stop close.

        This lets the dashboard SSE stream immediately notify the browser so it
        can refresh the open-positions panel without waiting for the next 5-second poll.
        """
        tick_data = {
            "symbol": symbol,
            "action": "SELL",
            "reason": reason,
            "metadata": {**(self._latest_ticks.get(symbol, {}).get("metadata") or {}), "price": float(price)},
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self._latest_ticks[symbol] = tick_data
        for _cb in self._tick_callbacks:
            try:
                _cb(tick_data)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Tick callback error in _notify_close (non-fatal): {exc}")

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
            price = ticker.last

        if self._mode == "live":
            sell_amount = position.amount
            try:
                order = self._exchange.place_order(symbol, "sell", sell_amount)
            except InsufficientFundsError as exc:
                # Upbit reports insufficient funds — sync tracked amount with the
                # actual exchange balance, then retry once with the real amount.
                actual = self.sync_position_amount(symbol)
                if actual is None:
                    # Balance query failed — keep and retry next tick
                    logger.error(
                        f"Sell order failed and balance sync failed: {exc} "
                        f"— position kept, retrying next tick | symbol={symbol}",
                    )
                    self._telegram.send_risk_alert(
                        f"⚠️ Sell order failed for {symbol}: {exc}. "
                        "Position kept — will retry on next tick."
                    )
                    return
                if actual <= 0:
                    # No coins on exchange — remove ghost position
                    logger.error(
                        f"Sell failed and zero balance confirmed — removing ghost position | symbol={symbol}",
                    )
                    self._telegram.send_risk_alert(
                        f"⚠️ {symbol} 잔고 0 확인 — 고스트 포지션 제거. 거래소 계좌를 확인하세요."
                    )
                    pnl = self._portfolio.close_position(symbol, price, commission=Decimal("0"))
                    self._risk_manager.on_position_closed(pnl)
                    self._journal.record(
                        TradeRecord(
                            symbol=symbol,
                            side=position.side,
                            amount=position.amount,
                            entry_price=position.entry_price,
                            exit_price=price,
                            entry_time=position.entry_time,
                            exit_time=datetime.now(UTC),
                            pnl=pnl,
                            commission=Decimal("0"),
                            reason="ghost_removed",
                        )
                    )
                    return
                logger.warning(
                    f"InsufficientFunds for {symbol}: tracked={float(sell_amount):.4f}, "
                    f"actual={float(actual):.4f} — retrying with actual balance",
                )
                try:
                    order = self._exchange.place_order(symbol, "sell", actual)
                except Exception as retry_exc:
                    # Retry also failed — re-sync to check if coins are now gone
                    actual2 = self.sync_position_amount(symbol)
                    if actual2 is not None and actual2 <= 0:
                        logger.error(
                            f"Retry sell failed and zero balance confirmed — removing ghost position | symbol={symbol}",
                        )
                        self._telegram.send_risk_alert(
                            f"⚠️ {symbol} 잔고 0 확인 — 고스트 포지션 제거. 거래소 계좌를 확인하세요."
                        )
                        pnl = self._portfolio.close_position(symbol, price, commission=Decimal("0"))
                        self._risk_manager.on_position_closed(pnl)
                        self._journal.record(
                            TradeRecord(
                                symbol=symbol,
                                side=position.side,
                                amount=position.amount,
                                entry_price=position.entry_price,
                                exit_price=price,
                                entry_time=position.entry_time,
                                exit_time=datetime.now(UTC),
                                pnl=pnl,
                                commission=Decimal("0"),
                                reason="ghost_removed",
                            )
                        )
                        return
                    logger.error(
                        f"Retry sell also failed: {retry_exc} — position kept, retrying next tick "
                        f"| symbol={symbol} amount={float(actual):.4f}",
                    )
                    self._telegram.send_risk_alert(
                        f"⚠️ Retry sell failed for {symbol}: {retry_exc}. "
                        "Position kept — will retry on next tick."
                    )
                    return
                sell_amount = actual
            except Exception as exc:
                # Keep position in tracking and retry on the next tick.
                # Removing the position here would leave the coin stranded on the
                # exchange with no SL/TP oversight — far more dangerous than retrying.
                logger.error(
                    f"Sell order failed: {exc} — position kept, retrying next tick "
                    f"| symbol={symbol} amount={float(sell_amount):.4f}",
                )
                self._telegram.send_risk_alert(
                    f"⚠️ Sell order failed for {symbol}: {exc}. "
                    "Position kept — will retry on next tick."
                )
                return
            fill_price = order.price if order.price > 0 else price
            commission = fill_price * sell_amount * self._PAPER_COMMISSION_RATE
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

    def sync_position_amount(self, symbol: str) -> Decimal | None:
        """
        Query the actual coin balance from the exchange and update the tracked
        position amount to match.  Returns the corrected amount, or None if the
        position does not exist or the balance query fails.

        Use this when an InsufficientFunds error reveals that the tracked amount
        is higher than the real holdings (e.g. after a partial fill or rounding).
        """
        if not self._portfolio.has_position(symbol):
            logger.warning("sync_position_amount called but no position found", symbol=symbol)
            return None
        coin = symbol.split("/")[0]
        try:
            bal = self._exchange.get_balance()
            coin_bal = bal.get(coin)
            available = Decimal(str(coin_bal.free)) if coin_bal is not None else Decimal("0")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Balance query failed during position amount sync: {exc}", symbol=symbol)
            return None

        if available <= 0:
            logger.warning(
                "sync_position_amount: zero balance — position may be fully sold on exchange",
                symbol=symbol,
            )
            return Decimal("0")

        pos = self._portfolio.get_position(symbol)
        if available == pos.amount:
            return available  # already in sync

        self._portfolio.update_amount(symbol, available)
        self._telegram.send_risk_alert(
            f"📊 {symbol} 포지션 수량 업비트 잔고 기준으로 수정: "
            f"{float(pos.amount):.4f} → {float(available):.4f}"
        )
        return available

    # ── Internal: dynamic symbol refresh ─────────────────────────────────────

    def _refresh_symbols(self) -> None:
        """
        Fetch top-N symbols by 24h volume from the exchange and update the
        scheduler to track them. Dropped symbols with open positions are kept.
        """
        try:
            new_top: list[str] = self._exchange.get_top_symbols_by_volume(self._top_n_symbols)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Symbol refresh failed: {exc}")
            return

        new_set = set(new_top) - self._symbol_blacklist

        # If user pinned specific symbols, auto-refresh cannot add or remove them.
        # new_set becomes exactly the pinned set, so added/dropped are always empty.
        if self._pinned_symbols:
            new_set = set(self._pinned_symbols) - self._symbol_blacklist

        added = new_set - self._tick_symbols
        dropped = self._tick_symbols - new_set

        # Remove dropped symbols — skip any with an open position
        actually_removed: set[str] = set()
        for sym in dropped:
            if self._portfolio.has_position(sym):
                logger.info("Symbol refresh: keeping dropped symbol (open position)", symbol=sym)
                continue
            job_id = f"tick_{sym.replace('/', '_')}"
            try:
                self._scheduler.remove_job(job_id)
            except Exception:  # noqa: BLE001
                pass
            self._tick_symbols.discard(sym)
            self._latest_ticks.pop(sym, None)
            actually_removed.add(sym)

        # Add new symbols — 초기 등록 때와 동일한 이유로, 새로 추가되는 심볼들의
        # 최초 실행 시각도 서로 어긋나게 분산해 API 호출이 한 번에 몰리지 않게 한다.
        added_list = list(added)
        stagger_seconds = self._tick_interval / len(added_list) if added_list else 0.0
        now = datetime.now(UTC)
        for i, sym in enumerate(added_list):
            job_id = f"tick_{sym.replace('/', '_')}"
            self._scheduler.add_job(
                self.tick,
                trigger="interval",
                seconds=self._tick_interval,
                next_run_time=now + timedelta(seconds=i * stagger_seconds),
                args=[sym],
                id=job_id,
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
            self._tick_symbols.add(sym)

        # Update sync anchor and preserve 24h-ranked order for display
        if new_top:
            self._sync_anchor = new_top[0]
            self._ranked_symbols = new_top

        if added or actually_removed:
            logger.info(
                "Symbol list refreshed",
                added=sorted(added),
                removed=sorted(actually_removed),
                active=sorted(self._tick_symbols),
            )

    # ── Internal: daily report ────────────────────────────────────────────────

    def _send_daily_report(self) -> None:
        """Scheduled job: send Telegram daily PnL summary and save Markdown report file."""
        # 1. Save Markdown report file + run scheduled tasks
        try:
            from src.report.generator import DailyReportGenerator  # noqa: PLC0415
            journal_path = getattr(self._journal, "_store", None)
            db_path = (
                str(getattr(journal_path, "_db_path", "data/journal.db"))
                if journal_path is not None
                else "data/journal.db"
            )
            symbols = list(self._pinned_symbols) or list(self._tick_symbols)
            gen = DailyReportGenerator(
                journal_path=db_path, symbols=symbols or None, run_tasks=True
            )
            report_path = gen.generate()
            logger.info("Daily Markdown report saved", path=str(report_path))

            # 실행된 작업이 있으면 Telegram으로 별도 알림
            executed = [r for r in gen.last_task_results if r.status == "executed"]
            if executed and self._telegram.is_enabled:
                lines = ["📋 <b>향후 과제 자동 실행 결과</b>", ""]
                for r in executed:
                    lines.append(r.to_telegram())
                    lines.append("")
                self._telegram.send("\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to save daily Markdown report", error=str(exc))

        # 2. Send Telegram summary
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
            logger.error("Failed to send daily Telegram report", error=str(exc))

    # ── Internal: daily news-sentiment tuning ─────────────────────────────────

    def _run_news_tuning(self) -> None:
        """
        Scheduled job (1일 1회): RSS 뉴스 헤드라인의 강세/약세 키워드 빈도로
        감성 점수를 내고, ScheduledTaskRunner(거래 건수 기준)와 별개로
        Vol/RSI 진입 문턱을 조정한다. dry_run=True(기본값)면 실제로 반영하지
        않고 텔레그램으로 시뮬레이션 결과만 보낸다.
        """
        try:
            from src.report.news_tuner import run_daily_news_tuning  # noqa: PLC0415

            result = run_daily_news_tuning(dry_run=self._news_tuning_dry_run)
            logger.info("News tuning result", status=result.status, detail=result.detail)
            if self._telegram.is_enabled:
                self._telegram.send(result.to_telegram())
        except Exception as exc:  # noqa: BLE001
            logger.error("News tuning job failed", error=str(exc))

    # ── Internal: auto-tuning restart watch ──────────────────────────────────

    def _check_pending_restart(self) -> None:
        """
        Scheduled job (frequent, independent of the daily report cron):
        if ScheduledTaskRunner has flagged a live_params change, notify and
        stop the engine so the watchdog relaunches (~30s) with the new
        .strategy_params.json — this is what makes auto-tuning "immediate"
        rather than waiting for the next daily report window.
        """
        try:
            from src.config import live_params  # noqa: PLC0415

            if live_params.restart_requested():
                reason = live_params.read_restart_reason() or "파라미터 자동 조정"
                if self._telegram.is_enabled:
                    self._telegram.send(
                        "🔄 <b>파라미터 자동 조정 반영을 위해 재시작합니다</b>\n"
                        f"{reason}\n워치독이 ~30초 내 새 파라미터로 재기동합니다."
                    )
                live_params.clear_restart_request()
                logger.warning("Restart requested by auto-tuning — stopping engine", reason=reason)
                self.stop()
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to process auto-tuning restart request", error=str(exc))

    # ── Internal: weekly report ───────────────────────────────────────────────

    def _send_weekly_report(self) -> None:
        """Scheduled job: send Telegram weekly PnL + auto-tuning digest."""
        try:
            from src.report.generator import DailyReportGenerator  # noqa: PLC0415
            journal_path = getattr(self._journal, "_store", None)
            db_path = (
                str(getattr(journal_path, "_db_path", "data/journal.db"))
                if journal_path is not None
                else "data/journal.db"
            )
            gen = DailyReportGenerator(journal_path=db_path)
            report_path, summary = gen.generate_weekly()
            logger.info("Weekly Markdown report saved", path=str(report_path))
            if self._telegram.is_enabled:
                self._telegram.send(summary)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send weekly report", error=str(exc))

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
        registered = 0

        # ── Sync external holdings into portfolio ─────────────────────────────
        if self._mode == "live":
            try:
                balance = self._exchange.get_balance()
                # Check ALL exchange balances — not just tracked symbols.
                # This recovers positions that were dropped due to a failed sell order
                # or that belong to symbols outside the current top-N list.
                for base_coin, bal_entry in balance.items():
                    if base_coin in ("info", "free", "used", "total", "KRW"):
                        continue
                    sym = f"{base_coin}/KRW"
                    if sym in local_symbols:
                        continue  # already tracked
                    free_amount = Decimal(str(bal_entry.free))
                    if free_amount <= Decimal("0"):
                        continue
                    # Register with current market price as synthetic entry
                    try:
                        ticker = self._exchange.get_ticker(sym)
                        entry_price = ticker.last
                    except Exception:  # noqa: BLE001
                        continue
                    # Skip dust holdings worth 5,001 KRW or less
                    if free_amount * entry_price <= Decimal("5001"):
                        logger.info(
                            "Reconciliation: skipping dust holding",
                            symbol=sym,
                            value_krw=float(free_amount * entry_price),
                        )
                        continue
                    # Apply same SL/TP as normal opens so reconciled positions auto-close
                    recon_sl = entry_price * (Decimal("1") - Decimal(str(self._settings.max_position_risk)))
                    recon_sl_dist = entry_price - recon_sl
                    recon_tp_dist = max(recon_sl_dist * Decimal("2"), entry_price * Decimal("0.015"))
                    recon_tp = entry_price + recon_tp_dist
                    self._portfolio.open_position(
                        symbol=sym,
                        side="buy",
                        amount=free_amount,
                        entry_price=entry_price,
                        commission=Decimal("0"),
                        stop_loss=recon_sl,
                        take_profit=recon_tp,
                        trailing_stop_pct=self._trailing_stop_pct,
                    )
                    self._risk_manager.on_position_opened()
                    logger.info(
                        "Reconciliation: registered external holding",
                        symbol=sym,
                        amount=float(free_amount),
                        entry_price=float(entry_price),
                    )
                    registered += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Reconciliation: balance fetch failed", error=str(exc))

        # Warn about local positions for symbols outside the trading list
        local_symbols = set(self._portfolio.open_symbols())  # refresh after registration
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

        # ── Sync cash immediately after reconciliation to fix negative values ──
        if self._mode == "live":
            try:
                bal = self._exchange.get_balance()
                krw = bal.get("KRW")
                if krw is not None:
                    self._portfolio.sync_cash(Decimal(str(krw.free)))
                    logger.info(
                        "Cash synced after reconciliation",
                        cash=float(krw.free),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Post-reconciliation cash sync failed", error=str(exc))

        logger.info(
            "Position reconciliation complete",
            local_open=len(local_symbols),
            tracked_symbols=len(tracked),
            ghost_symbols=len(ghost_symbols),
            registered_from_exchange=registered,
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
        """Scheduled job: log liveness + send hourly summary or heartbeat."""
        try:
            report = self._portfolio.pnl_report()
            stats = self._journal.stats()
            circuit_state = self._circuit_breaker.state.name
            logger.info(
                "Heartbeat",
                open_positions=report["open_positions"],
                cash=report["cash"],
                realized_pnl=report["realized_pnl"],
                circuit=circuit_state,
            )
            if getattr(self._telegram, '_hourly_summary', False):
                # Build per-position detail with live prices
                positions_detail: list[dict] = []
                for sym in self._portfolio.open_symbols():
                    pos = self._portfolio.get_position(sym)
                    if pos is None:
                        continue
                    try:
                        ticker = self._exchange.get_ticker(sym)
                        current = ticker.last
                    except Exception:  # noqa: BLE001
                        current = pos.entry_price
                    pnl_val = float(pos.unrealized_pnl(current))
                    pnl_pct = pos.unrealized_pnl_pct(current)
                    positions_detail.append({
                        "symbol": sym,
                        "entry_price": float(pos.entry_price),
                        "current_price": float(current),
                        "amount": float(pos.amount),
                        "pnl": pnl_val,
                        "pnl_pct": pnl_pct,
                        "stop_loss": float(pos.stop_loss) if pos.stop_loss else 0.0,
                        "take_profit": float(pos.take_profit) if pos.take_profit else 0.0,
                    })

                self._telegram.flush_summary(
                    mode=self._mode,
                    paused=self.is_paused,
                    circuit_state=circuit_state,
                    open_positions=report["open_positions"],
                    cash=float(report["cash"]),
                    realized_pnl=float(report["realized_pnl"]),
                    total_trades=stats["total_trades"],
                    wins=stats["wins"],
                    losses=stats["losses"],
                    win_rate_pct=stats["win_rate_pct"],
                    profit_factor=float(stats["profit_factor"]) if stats["profit_factor"] != float("inf") else 999.0,
                    avg_win=stats["avg_win"],
                    avg_loss=stats["avg_loss"],
                    positions_detail=positions_detail,
                )
            else:
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
