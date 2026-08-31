"""
브레이크아웃 스캐너 프로세스 진입점 (`python -m src.breakout.runner`).

라이브 엔진(src/main.py, RegimeAdaptiveStrategy)과는 완전히 독립된 프로세스로
실행하는 것을 전제로 설계했다 — 별도의 docker-compose 서비스(breakout-scanner)로
기동하며, 이 프로세스를 새로 배포/재시작해도 라이브 트레이딩 컨테이너는 전혀
건드리지 않는다.

항상 PAPER 모드로만 동작한다 (라이브 자본에 접근하지 않음, 실제 주문을 넣지
않음). Upbit 공개 엔드포인트(티커/캔들 조회)만 쓰므로 API 키 없이도 동작하지만,
.env에 이미 설정된 UPBIT_ACCESS_KEY/SECRET을 그대로 재사용해도 무방하다(조회만
하고 주문은 절대 넣지 않으므로 영향 없음).
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal

from loguru import logger

from src.breakout.detector import BreakoutConfig
from src.breakout.exits import ExitConfig
from src.breakout.scanner import BreakoutScanner, ScannerConfig
from src.config.settings import get_settings
from src.exchange.upbit import UpbitClient
from src.portfolio.tracker import PortfolioTracker
from src.utils.telegram import TelegramClient


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="브레이크아웃 조기진입 페이퍼 스캐너 (가격/거래량 기반, 라이브 엔진과 독립 프로세스)"
    )
    parser.add_argument(
        "--interval-seconds", type=int, default=60,
        help="틱(청산 점검) 주기(초). 기본 60초",
    )
    parser.add_argument(
        "--scan-every-n-ticks", type=int, default=5,
        help="신규 진입 스캔은 몇 틱마다 수행할지. 기본 5 (interval 60초 기준 5분마다, 5분봉 주기와 일치)",
    )
    parser.add_argument("--initial-capital-krw", type=float, default=1_000_000.0,
                         help="페이퍼 계좌 초기 자본(KRW). 라이브 자본과 무관한 가상 값")
    parser.add_argument("--position-size-krw", type=float, default=100_000.0,
                         help="포지션당 페이퍼 매수 금액(KRW)")
    parser.add_argument("--max-concurrent-positions", type=int, default=3)
    parser.add_argument("--base-window-bars", type=int, default=48)
    parser.add_argument("--base-range-max-pct", type=float, default=8.0)
    parser.add_argument("--breakout-margin-pct", type=float, default=1.5)
    parser.add_argument("--vol-mult", type=float, default=5.0)
    parser.add_argument("--max-move-from-base-low-pct", type=float, default=20.0)
    parser.add_argument("--min-quote-volume-krw", type=float, default=10_000_000_000.0)
    parser.add_argument(
        "--trailing-stop-pct", type=float, default=12.0,
        help="신고가 대비 트레일링 스탑 폭(%%). 큰 움직임을 끝까지 잡기 위해 넓게 잡음 "
             "(사용자 확정 범위 10~15%%, 기본값 12%%)",
    )
    parser.add_argument("--initial-stop-loss-pct", type=float, default=3.0)
    parser.add_argument("--time-stop-minutes", type=int, default=60)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    settings = get_settings()

    exchange = UpbitClient(
        access_key=settings.upbit_access_key,
        secret_key=settings.upbit_secret_key,
    )
    telegram = TelegramClient(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        hourly_summary=False,  # 브레이크아웃 이벤트는 빈도가 낮아 즉시 알림으로 충분
    )
    portfolio = PortfolioTracker(initial_cash=Decimal(str(args.initial_capital_krw)))

    detector_cfg = BreakoutConfig(
        base_window_bars=args.base_window_bars,
        base_range_max_pct=args.base_range_max_pct,
        breakout_margin_pct=args.breakout_margin_pct,
        vol_mult=args.vol_mult,
        max_move_from_base_low_pct=args.max_move_from_base_low_pct,
        min_quote_volume_krw=args.min_quote_volume_krw,
    )
    exit_cfg = ExitConfig(
        trailing_stop_pct=args.trailing_stop_pct,
        initial_stop_loss_pct=args.initial_stop_loss_pct,
        time_stop_minutes=args.time_stop_minutes,
    )
    scanner_cfg = ScannerConfig(
        max_concurrent_positions=args.max_concurrent_positions,
        position_size_krw=Decimal(str(args.position_size_krw)),
    )

    logger.info(
        "Breakout scanner starting (PAPER mode, independent process)",
        initial_capital_krw=args.initial_capital_krw,
        trailing_stop_pct=args.trailing_stop_pct,
        max_concurrent_positions=args.max_concurrent_positions,
    )
    telegram.send(
        "🟢 <b>브레이크아웃 스캐너 시작</b> (PAPER, 라이브 봇과 독립 프로세스)\n"
        f"트레일링 {args.trailing_stop_pct:.1f}%  |  최대 동시 진입 {args.max_concurrent_positions}건  |  "
        f"페이퍼 초기자본 {args.initial_capital_krw:,.0f}원"
    )

    scanner = BreakoutScanner(
        exchange=exchange,
        telegram=telegram,
        portfolio=portfolio,
        detector_cfg=detector_cfg,
        exit_cfg=exit_cfg,
        scanner_cfg=scanner_cfg,
    )
    scanner.run_forever(
        interval_seconds=args.interval_seconds,
        scan_every_n_ticks=args.scan_every_n_ticks,
    )
    return 0  # pragma: no cover — run_forever loops forever; unreachable in practice


if __name__ == "__main__":
    sys.exit(main())
