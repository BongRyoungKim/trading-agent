"""
Trading Agent entry point.
Wires all components and starts the trading engine.
"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal

from src.config.settings import get_settings
from src.utils.logger import logger, setup_logger
from src.utils import prevent_sleep


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Trading Agent")
    p.add_argument(
        "--mode",
        choices=["live", "paper", "backtest"],
        default=None,
        help="Override trading mode from environment",
    )
    p.add_argument(
        "--symbols",
        nargs="+",
        default=["BTC/KRW"],
        help="Symbols to trade (default: BTC/KRW)",
    )
    p.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Polling interval in seconds (default: 60)",
    )
    p.add_argument(
        "--strategy",
        default="MACrossoverStrategy",
        help=(
            "Strategy class name from registry "
            "(default: MACrossoverStrategy). "
            "Run --list-strategies to see all options."
        ),
    )
    p.add_argument(
        "--strategy-params",
        default="{}",
        dest="strategy_params",
        help='JSON string of extra strategy constructor params (default: {}).',
    )
    p.add_argument(
        "--list-strategies",
        action="store_true",
        dest="list_strategies",
        help="Print all registered strategy names and exit.",
    )
    p.add_argument(
        "--slippage-bps",
        type=float,
        default=5.0,
        dest="slippage_bps",
        help=(
            "Paper-trading slippage in basis points (default: 5). "
            "Applied as bid-ask spread cost. Set 0 to disable slippage."
        ),
    )
    p.add_argument(
        "--skip-checks",
        action="store_true",
        dest="skip_checks",
        help="Skip pre-flight startup checks (not recommended for live trading).",
    )
    p.add_argument(
        "--trailing-stop-pct",
        type=float,
        default=None,
        dest="trailing_stop_pct",
        help=(
            "Trailing stop percentage (e.g. 2.0 = 2%%). "
            "Stop-loss ratchets up as price rises. Default: disabled."
        ),
    )
    p.add_argument(
        "--health-port",
        type=int,
        default=8080,
        dest="health_port",
        help=(
            "Port for the health-check HTTP server (default: 8080). "
            "Set 0 to disable. Exposes GET /health and GET /ready."
        ),
    )
    p.add_argument(
        "--symbol-strategy",
        action="append",
        default=[],
        dest="symbol_strategies",
        metavar="SYMBOL=STRATEGY",
        help=(
            "Assign a strategy to a specific symbol, e.g. "
            "'BTC/USDT=MACrossoverStrategy'. "
            "Repeat for multiple symbols. "
            "Symbols without an explicit assignment use --strategy."
        ),
    )
    p.add_argument(
        "--heartbeat-interval",
        type=int,
        default=1800,
        dest="heartbeat_interval",
        help=(
            "Seconds between Telegram heartbeat messages (default: 1800). "
            "Set 0 to disable."
        ),
    )
    p.add_argument(
        "--market-hours",
        default=None,
        dest="market_hours",
        metavar="HH:MM-HH:MM",
        help=(
            "Restrict trading to a UTC time window, e.g. '09:00-18:00'. "
            "Omit for 24/7 trading (default for crypto)."
        ),
    )
    p.add_argument(
        "--trading-days",
        default="mon-sun",
        dest="trading_days",
        help="Trading days, e.g. 'mon-fri' or 'mon,wed,fri'. Default: mon-sun.",
    )
    p.add_argument(
        "--dashboard-port",
        type=int,
        default=0,
        dest="dashboard_port",
        help=(
            "Port for the web dashboard (default: 0 = disabled). "
            "e.g. --dashboard-port 8000 → http://localhost:8000"
        ),
    )
    p.add_argument(
        "--telegram-bot",
        action="store_true",
        dest="telegram_bot",
        help="Enable Telegram bot incoming command handling (/status, /pause, /resume, etc.).",
    )
    p.add_argument(
        "--websocket",
        action="store_true",
        dest="websocket",
        help=(
            "Use Binance WebSocket kline feed instead of REST polling. "
            "Triggers tick evaluation on candle close — more responsive than interval polling. "
            "Only supported for Binance exchange."
        ),
    )
    p.add_argument(
        "--paper-capital",
        type=float,
        default=100000.0,
        dest="paper_capital",
        help=(
            "Initial capital (KRW) for paper trading (default: 100,000). "
            "Ignored in live mode — actual exchange balance is used."
        ),
    )
    p.add_argument(
        "--top-symbols",
        type=int,
        default=0,
        dest="top_symbols",
        help=(
            "Dynamically track top-N symbols by 24h trading volume (default: 0 = disabled). "
            "When set, --symbols is used only for startup validation; "
            "the engine manages the universe via auto-refresh every 10 min."
        ),
    )
    p.add_argument(
        "--exclude-symbols",
        nargs="+",
        default=[],
        dest="exclude_symbols",
        metavar="SYMBOL",
        help="Symbols to never trade (blacklist). e.g. --exclude-symbols DOGE/KRW ELSA/KRW",
    )
    p.add_argument(
        "--sl-floor-pct",
        type=float,
        default=3.0,
        dest="sl_floor_pct",
        help="Max stop-loss distance from entry, in percent (default: 3.0).",
    )
    p.add_argument(
        "--sl-ceiling-pct",
        type=float,
        default=1.5,
        dest="sl_ceiling_pct",
        help="Min stop-loss distance from entry, in percent (default: 1.5).",
    )
    p.add_argument(
        "--atr-multiplier",
        type=float,
        default=2.0,
        dest="atr_multiplier",
        help="ATR multiplier used to size the stop-loss distance (default: 2.0).",
    )
    p.add_argument(
        "--tp-rr-multiplier",
        type=float,
        default=1.5,
        dest="tp_rr_multiplier",
        help="Take-profit distance as a multiple of the SL distance (default: 1.5).",
    )
    p.add_argument(
        "--weekly-report-day",
        default="mon",
        dest="weekly_report_day",
        help="Day of week for the weekly Telegram report, e.g. 'mon' (default: mon). "
             "Set to '' to disable.",
    )
    p.add_argument(
        "--weekly-report-hour",
        type=int,
        default=9,
        dest="weekly_report_hour",
        help="Hour (0-23) to send the weekly Telegram report (default: 9).",
    )
    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    setup_logger()

    if args.list_strategies:
        from src.strategy.registry import list_strategies
        import src.strategy  # noqa: F401 — triggers @register decorators
        print("Registered strategies:")
        for name in list_strategies():
            print(f"  {name}")
        return

    settings = get_settings()

    # Allow CLI to override trading mode
    if args.mode:
        import os
        os.environ["TRADING_MODE"] = args.mode
        get_settings.cache_clear()
        settings = get_settings()

    effective_mode = settings.trading_mode

    try:
        strategy_params: dict = json.loads(args.strategy_params)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--strategy-params is not valid JSON: {exc}") from exc

    logger.info(
        "Trading Agent starting",
        mode=effective_mode,
        exchange=settings.exchange,
        symbols=args.symbols,
        strategy=args.strategy,
    )

    # ── Build components ──────────────────────────────────────────────────────
    from src.exchange.binance import BinanceClient
    from src.exchange.upbit import UpbitClient
    from src.portfolio.journal_store import SQLiteJournalStore
    from src.portfolio.position_store import SQLitePositionStore
    from src.portfolio.tracker import PortfolioTracker
    from src.risk.manager import PortfolioState, RiskManager
    from src.risk.position_sizing import percent_of_equity
    from src.strategy.registry import get_strategy
    import src.strategy  # noqa: F401 — triggers @register decorators
    from src.utils.telegram import get_telegram_client

    if settings.exchange == "upbit":
        exchange = UpbitClient(
            access_key=settings.upbit_access_key,
            secret_key=settings.upbit_secret_key,
        )
    else:
        exchange = BinanceClient(
            api_key=settings.binance_api_key,
            secret_key=settings.binance_secret_key,
            testnet=effective_mode != "live",
        )

    if effective_mode == "live":
        try:
            balance = exchange.get_balance()
            krw = balance.get("KRW")
            initial_capital = Decimal(str(krw.free)) if krw and krw.free > 0 else Decimal("10000")
            logger.info("Initial capital set from exchange balance", krw=float(initial_capital))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch balance, using default capital", error=str(exc))
            initial_capital = Decimal("10000")
    else:
        initial_capital = Decimal(str(int(args.paper_capital)))
    position_store = SQLitePositionStore()
    portfolio = PortfolioTracker.from_store(initial_cash=initial_capital, store=position_store)

    restored_positions = len(portfolio.open_symbols())

    # When the service restarts with open positions, krw.free excludes the capital
    # locked in those positions.  Add the current market value of each restored
    # position so the risk manager starts from the true portfolio value and does
    # not compute a false drawdown that permanently blocks new orders.
    risk_capital = initial_capital
    if restored_positions and effective_mode == "live":
        for sym in portfolio.open_symbols():
            pos = portfolio.get_position(sym)
            if pos is None:
                continue
            try:
                ticker = exchange.get_ticker(sym)
                position_value = pos.amount * ticker.last
                risk_capital += position_value
                logger.info(
                    "Risk capital adjusted for restored position",
                    symbol=sym,
                    position_value=float(position_value),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Could not fetch ticker for restored position — using entry price",
                    symbol=sym,
                    error=str(exc),
                )
                risk_capital += pos.amount * pos.entry_price

    risk_state = PortfolioState(
        capital=risk_capital,
        peak_capital=risk_capital,
        open_positions=restored_positions,
    )
    if restored_positions:
        logger.info(
            "Risk state initialised with restored positions",
            open_positions=restored_positions,
            risk_capital=float(risk_capital),
        )
    risk_manager = RiskManager(settings, risk_state)

    if args.symbol_strategies:
        from src.strategy.symbol_router import SymbolStrategyRouter
        routes: dict = {}
        for item in args.symbol_strategies:
            if "=" not in item:
                raise SystemExit(
                    f"Invalid --symbol-strategy format: '{item}'. "
                    "Expected SYMBOL=STRATEGY_NAME (e.g. BTC/USDT=MACrossoverStrategy)"
                )
            sym, strat_name = item.split("=", 1)
            sym, strat_name = sym.strip(), strat_name.strip()
            routes[sym] = get_strategy(strat_name, symbol=sym, **strategy_params)
        # Symbols without an explicit mapping fall back to --strategy
        unrouted = [s for s in args.symbols if s not in routes]
        if unrouted:
            default_strategy = get_strategy(
                args.strategy, symbol=unrouted[0], **strategy_params
            )
            strategy = SymbolStrategyRouter(routes=routes, default=default_strategy)
        else:
            strategy = SymbolStrategyRouter(routes=routes)
    else:
        strategy = get_strategy(args.strategy, symbol=args.symbols[0], **strategy_params)
    telegram = get_telegram_client()
    journal_store = SQLiteJournalStore()

    from src.exchange.slippage import SlippageConfig
    slippage_config = SlippageConfig(spread_bps=args.slippage_bps)

    from src.utils.market_hours import MarketHoursConfig
    if args.market_hours:
        market_hours = MarketHoursConfig(
            enabled=True,
            trading_hours=args.market_hours,
            trading_days=args.trading_days,
        )
        logger.info(
            "Market hours configured",
            hours=args.market_hours,
            days=args.trading_days,
        )
    else:
        market_hours = MarketHoursConfig(enabled=False)

    # ── Pre-flight checks ─────────────────────────────────────────────────────
    if not args.skip_checks:
        from src.utils.startup_check import StartupChecker
        checker = StartupChecker()
        results = checker.run_all(
            settings=settings,
            exchange=exchange,
            symbols=args.symbols,
        )
        if not checker.print_summary(results):
            raise SystemExit("Startup checks failed. Use --skip-checks to bypass.")

    # ── Health check server ───────────────────────────────────────────────────
    if args.health_port:
        from src.health import start_health_server
        start_health_server(port=args.health_port)
        logger.info("Health server started", port=args.health_port)

    # ── Start engine ──────────────────────────────────────────────────────────
    from src.engine import TradingEngine
    engine = TradingEngine(
        settings=settings,
        exchange=exchange,
        strategy=strategy,
        risk_manager=risk_manager,
        portfolio=portfolio,
        telegram=telegram,
        journal_store=journal_store,
        slippage_config=slippage_config,
        market_hours=market_hours,
    )
    if args.trailing_stop_pct is not None:
        engine.trailing_stop_pct = args.trailing_stop_pct
    engine.sl_floor_pct = args.sl_floor_pct
    engine.sl_ceiling_pct = args.sl_ceiling_pct
    engine.atr_multiplier = args.atr_multiplier
    engine.tp_rr_multiplier = args.tp_rr_multiplier
    if args.exclude_symbols:
        engine.symbol_blacklist = args.exclude_symbols
        logger.info("Symbol blacklist applied", excluded=args.exclude_symbols)

    # ── Web Dashboard ─────────────────────────────────────────────────────────
    dashboard_port = args.dashboard_port or settings.dashboard_port
    if dashboard_port:
        from src.dashboard.server import start_dashboard_server
        from src.dashboard.state import get_dashboard_state
        get_dashboard_state().register_engine(engine)
        start_dashboard_server(port=dashboard_port)
        logger.info("Web dashboard started", port=dashboard_port, url=f"http://localhost:{dashboard_port}")

    # ── Telegram Bot ──────────────────────────────────────────────────────────
    if args.telegram_bot:
        from src.utils.telegram_bot import TelegramBotController
        bot = TelegramBotController(
            telegram=telegram,
            engine=engine,
            portfolio_fn=lambda: engine._portfolio.pnl_report(),  # noqa: SLF001
        )
        bot.start()

    # ── WebSocket Feed ────────────────────────────────────────────────────────
    if args.websocket:
        if settings.exchange != "binance":
            logger.warning(
                "WebSocket feed only supports Binance — ignoring --websocket flag",
                exchange=settings.exchange,
            )
        else:
            from src.exchange.websocket_feed import BinanceWebSocketFeed
            ws_feed = BinanceWebSocketFeed(
                symbols=args.symbols,
                interval=strategy.timeframe if hasattr(strategy, "timeframe") else "1m",
                on_candle_close=lambda sym, _candle: engine.tick(sym),
            )
            ws_feed.start()
            logger.info(
                "WebSocket feed active — ticks driven by candle close",
                symbols=args.symbols,
            )

    prevent_sleep.enable()
    try:
        engine.start(
            symbols=args.symbols,
            interval_seconds=args.interval,
            heartbeat_interval=args.heartbeat_interval or None,
            pin_symbols=(args.top_symbols == 0),
            weekly_report_day=(args.weekly_report_day or None),
            weekly_report_hour=args.weekly_report_hour,
            top_n_symbols=args.top_symbols,
        )
    finally:
        prevent_sleep.disable()


if __name__ == "__main__":
    main()
