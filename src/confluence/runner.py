"""
1d+4h+1h 컨플루언스 스캐너 프로세스 진입점 (`python -m src.confluence.runner`).

라이브 엔진(src/main.py, RegimeAdaptiveStrategy)과 완전히 독립된 프로세스로
실행한다 — 별도의 docker-compose 서비스(confluence-btc)로 기동하며, 이
프로세스를 새로 배포/재시작해도 라이브 트레이딩 컨테이너는 전혀 건드리지
않는다.

항상 PAPER 모드로만 동작(라이브 자본에 접근하지 않음) — 2026-09-23
"페이퍼 모드 우선(협상 불가)" 원칙, CLAUDE.md 하드게이트에 따름.
백테스트(전체 n=134, PF=1.509, Sharpe=2.611, outlier 점검 통과)가
아무리 좋아도 실거래 전환은 페이퍼 관찰 후 별도 결정.

청산 로직은 2026-09-23 재검증(1h/4h를 2017-10-01까지 확장 수집 후
train/validation/holdout 3구간 전부에서 트레일링 스탑이 원안 ATR손절보다
우수함을 확인)에 따라 트레일링 스탑(초기손절5%+신고가대비15%)으로
교체됨 — src/confluence/exits.py 참고.
"""
from __future__ import annotations

import argparse
from decimal import Decimal

from src.config.settings import get_settings
from src.confluence.exits import ConfluenceExitConfig
from src.confluence.scanner import ConfluenceScanner, ConfluenceScannerConfig
from src.exchange.upbit import UpbitClient
from src.portfolio.tracker import PortfolioTracker
from src.utils.logger import logger, setup_logger
from src.utils.telegram import TelegramClient


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="1d+4h+1h 컨플루언스 BTC 페이퍼 스캐너 (라이브 엔진과 독립 프로세스)"
    )
    parser.add_argument("--symbol", default="BTC/KRW")
    parser.add_argument(
        "--interval-seconds", type=int, default=300,
        help="틱(손절 점검) 주기(초). 기본 300초(5분)",
    )
    parser.add_argument(
        "--scan-every-n-ticks", type=int, default=12,
        help="국면 갱신+진입판정은 몇 틱마다 수행할지. 기본 12(interval 5분 기준 1시간마다)",
    )
    parser.add_argument("--initial-capital-krw", type=float, default=1_000_000.0)
    parser.add_argument("--position-size-krw", type=float, default=100_000.0)
    parser.add_argument("--breakout-window", type=int, default=8)
    parser.add_argument("--vol-mult", type=float, default=1.5)
    parser.add_argument("--initial-stop-pct", type=float, default=5.0)
    parser.add_argument("--trailing-stop-pct", type=float, default=15.0)
    parser.add_argument("--max-hold-hours", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    setup_logger()
    settings = get_settings()

    exchange = UpbitClient(
        access_key=settings.upbit_access_key,
        secret_key=settings.upbit_secret_key,
    )
    telegram = TelegramClient(
        bot_token=settings.telegram_bot_token,
        # 페이퍼 전용 채팅방이 설정돼 있으면 그쪽으로, 없으면 기존 메인
        # 채팅방으로 폴백(설정 전 기존 배포와 동일 동작 유지).
        chat_id=settings.telegram_paper_chat_id or settings.telegram_chat_id,
        hourly_summary=False,
    )
    portfolio = PortfolioTracker(initial_cash=Decimal(str(args.initial_capital_krw)))

    exit_cfg = ConfluenceExitConfig(
        initial_stop_pct=args.initial_stop_pct,
        trailing_stop_pct=args.trailing_stop_pct,
        max_hold_hours=args.max_hold_hours,
    )
    scanner_cfg = ConfluenceScannerConfig(
        symbol=args.symbol,
        position_size_krw=Decimal(str(args.position_size_krw)),
        breakout_window=args.breakout_window,
        vol_mult=args.vol_mult,
    )

    logger.info(
        "Confluence scanner starting (PAPER mode, independent process)",
        symbol=args.symbol, initial_capital_krw=args.initial_capital_krw,
    )
    telegram.send(
        "🟢 <b>1d+4h+1h 컨플루언스 스캐너 시작</b> (PAPER, 라이브 봇과 독립 프로세스)\n"
        f"대상 {args.symbol}  |  페이퍼 초기자본 {args.initial_capital_krw:,.0f}원"
    )

    scanner = ConfluenceScanner(
        exchange=exchange, telegram=telegram, portfolio=portfolio,
        exit_cfg=exit_cfg, scanner_cfg=scanner_cfg,
    )
    scanner.run_forever(
        interval_seconds=args.interval_seconds,
        scan_every_n_ticks=args.scan_every_n_ticks,
    )
    return 0  # pragma: no cover — run_forever loops forever; unreachable in practice


if __name__ == "__main__":
    raise SystemExit(main())
