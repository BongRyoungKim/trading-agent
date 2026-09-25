"""
온체인 넷플로우 스캐너 프로세스 진입점 (`python -m src.onchain.runner`).

라이브 엔진(src/main.py, RegimeAdaptiveStrategy)과 완전히 독립된 프로세스로
실행한다 — 별도의 docker-compose 서비스(onchain-btc)로 기동하며, 이 프로세스를
새로 배포/재시작해도 라이브 트레이딩 컨테이너는 전혀 건드리지 않는다.

항상 PAPER 모드로만 동작한다(라이브 자본에 접근하지 않음, 실제 주문을 넣지
않음) — 2026-09-22 계획서의 "페이퍼 모드 우선(협상 불가)" 결정에 따름.
CLAUDE.md 하드게이트("실매매 모드 실행 전 반드시 페이퍼 트레이딩으로 검증")를
따르는 것으로, 오늘 백테스트 결과(전체 2017-2026 n=161 PF=1.247 Sharpe=2.102)가
아무리 좋아도 이번 라운드는 페이퍼로만 배포한다. 실거래 전환은 페이퍼 결과를
몇 주 지켜본 뒤 별도 결정.
"""
from __future__ import annotations

import argparse
from decimal import Decimal

from src.config.settings import get_settings
from src.exchange.upbit import UpbitClient
from src.onchain.exits import OnchainExitConfig
from src.onchain.scanner import OnchainScanner, OnchainScannerConfig
from src.portfolio.tracker import PortfolioTracker
from src.utils.logger import logger, setup_logger
from src.utils.telegram import TelegramClient


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BTC 온체인 넷플로우 페이퍼 스캐너 (라이브 엔진과 독립 프로세스)"
    )
    parser.add_argument("--symbol", default="BTC/KRW")
    parser.add_argument(
        "--interval-seconds", type=int, default=900,
        help="틱(손절 점검) 주기(초). 기본 900초(15분)",
    )
    parser.add_argument(
        "--scan-every-n-ticks", type=int, default=96,
        help="넷플로우 재조회+진입판정은 몇 틱마다 수행할지. "
             "기본 96(interval 15분 기준 24시간마다, 신호가 일봉 단위인 백테스트와 일치)",
    )
    parser.add_argument("--initial-capital-krw", type=float, default=1_000_000.0,
                         help="페이퍼 계좌 초기 자본(KRW). 라이브 자본과 무관한 가상 값")
    parser.add_argument("--position-size-krw", type=float, default=100_000.0,
                         help="포지션당 페이퍼 매수 금액(KRW)")
    parser.add_argument("--entry-z-threshold", type=float, default=-1.5)
    parser.add_argument("--z-exit-threshold", type=float, default=0.0)
    parser.add_argument("--zscore-lookback-days", type=int, default=30)
    parser.add_argument("--atr-multiplier", type=float, default=2.5)
    parser.add_argument("--sl-ceiling-pct", type=float, default=3.0)
    parser.add_argument("--sl-floor-pct", type=float, default=10.0)
    parser.add_argument("--max-hold-days", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    # loguru 기본 핸들러는 구조화 필드(extra)를 안 그려주므로 반드시 필요
    # (breakout 스캐너에서 실제로 이걸 빼먹어 로그가 비었던 사고가 있었음).
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
        hourly_summary=False,  # 진입/청산이 하루 단위로 드물어 즉시 알림으로 충분
    )
    portfolio = PortfolioTracker(initial_cash=Decimal(str(args.initial_capital_krw)))

    exit_cfg = OnchainExitConfig(
        atr_multiplier=args.atr_multiplier,
        sl_ceiling_pct=args.sl_ceiling_pct,
        sl_floor_pct=args.sl_floor_pct,
        z_exit_threshold=args.z_exit_threshold,
        max_hold_days=args.max_hold_days,
    )
    scanner_cfg = OnchainScannerConfig(
        symbol=args.symbol,
        position_size_krw=Decimal(str(args.position_size_krw)),
        entry_z_threshold=args.entry_z_threshold,
        zscore_lookback_days=args.zscore_lookback_days,
    )

    logger.info(
        "Onchain netflow scanner starting (PAPER mode, independent process)",
        symbol=args.symbol,
        initial_capital_krw=args.initial_capital_krw,
        entry_z_threshold=args.entry_z_threshold,
    )
    telegram.send(
        "🟢 <b>온체인 넷플로우 스캐너 시작</b> (PAPER, 라이브 봇과 독립 프로세스)\n"
        f"대상 {args.symbol}  |  진입 z≤{args.entry_z_threshold}  |  "
        f"페이퍼 초기자본 {args.initial_capital_krw:,.0f}원"
    )

    scanner = OnchainScanner(
        exchange=exchange,
        telegram=telegram,
        portfolio=portfolio,
        exit_cfg=exit_cfg,
        scanner_cfg=scanner_cfg,
    )
    scanner.run_forever(
        interval_seconds=args.interval_seconds,
        scan_every_n_ticks=args.scan_every_n_ticks,
    )
    return 0  # pragma: no cover — run_forever loops forever; unreachable in practice


if __name__ == "__main__":
    raise SystemExit(main())
