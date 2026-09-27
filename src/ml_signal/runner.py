"""
다변량 ML 신호(RandomForest) 스캐너 프로세스 진입점 (`python -m src.ml_signal.runner`).

라이브 엔진(src/main.py, RegimeAdaptiveStrategy)과 완전히 독립된 프로세스로
실행한다 — 별도의 docker-compose 서비스(ml-signal)로 기동하며, onchain-btc·
confluence-btc와 동일한 설계 원칙(항상 PAPER, 실제 자금 접근 없음).

CLAUDE.md 하드게이트("실매매 모드 실행 전 반드시 페이퍼 트레이딩으로 검증")에
따라 이번 라운드도 페이퍼 전용 배포. 백테스트 근거(2026-09-27 리서치,
27개 종목 6구간 워크포워드 — 전 구간에서 "아무 날에나 사서 5일 보유"
기준선 대비 우위 확인, 단 AUC는 0.51~0.63으로 약함)를 CLAUDE.md 문서에
기록하고, 몇 주~몇 달 페이퍼 관찰 후 실거래 전환 여부를 별도 결정한다.

모델은 이 프로세스 안에서 절대 학습하지 않는다 — scripts/train_ml_signal_
model.py로 미리 학습해 저장해둔 data/ml_signal_model.joblib를 읽기만 한다.
"""
from __future__ import annotations

import argparse
from decimal import Decimal

from src.config.settings import get_settings
from src.exchange.upbit import UpbitClient
from src.ml_signal.exits import MLSignalExitConfig
from src.ml_signal.model import load_model
from src.ml_signal.scanner import DEFAULT_SYMBOLS, MLSignalScanner, MLSignalScannerConfig
from src.portfolio.tracker import PortfolioTracker
from src.utils.logger import logger, setup_logger
from src.utils.telegram import TelegramClient


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="다변량 ML 신호(RandomForest) 페이퍼 스캐너 (라이브 엔진과 독립 프로세스)"
    )
    parser.add_argument(
        "--interval-seconds", type=int, default=900,
        help="틱(안전장치 손절 점검) 주기(초). 기본 900초(15분)",
    )
    parser.add_argument(
        "--scan-every-n-ticks", type=int, default=96,
        help="전 종목 신호 재계산+진입판정은 몇 틱마다 수행할지. "
             "기본 96(interval 15분 기준 24시간마다, 일봉 신호와 일치)",
    )
    parser.add_argument("--initial-capital-krw", type=float, default=1_000_000.0,
                         help="페이퍼 계좌 초기 자본(KRW). 라이브 자본과 무관한 가상 값")
    parser.add_argument("--position-size-krw", type=float, default=200_000.0,
                         help="포지션당 페이퍼 매수 금액(KRW)")
    parser.add_argument("--max-concurrent-positions", type=int, default=5)
    parser.add_argument("--entry-threshold", type=float, default=0.5,
                         help="모델 예측확률이 이 값을 넘으면 진입 (리서치 기본값 0.5)")
    parser.add_argument("--hold-days", type=int, default=5)
    parser.add_argument("--safety-stop-pct", type=float, default=20.0)
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
        chat_id=settings.telegram_paper_chat_id or settings.telegram_chat_id,
        hourly_summary=False,
    )
    portfolio = PortfolioTracker(initial_cash=Decimal(str(args.initial_capital_krw)))
    model = load_model()

    exit_cfg = MLSignalExitConfig(
        hold_days=args.hold_days,
        safety_stop_pct=args.safety_stop_pct,
    )
    scanner_cfg = MLSignalScannerConfig(
        symbols=DEFAULT_SYMBOLS,
        position_size_krw=Decimal(str(args.position_size_krw)),
        max_concurrent_positions=args.max_concurrent_positions,
        entry_threshold=args.entry_threshold,
    )

    logger.info(
        "ML signal scanner starting (PAPER mode, independent process)",
        symbols=len(scanner_cfg.symbols),
        initial_capital_krw=args.initial_capital_krw,
        entry_threshold=args.entry_threshold,
        model_trained_at=model.trained_at,
    )
    telegram.send(
        "🤖 <b>ML 다변량 신호 스캐너 시작</b> (PAPER, 라이브 봇과 독립 프로세스)\n"
        f"종목 {len(scanner_cfg.symbols)}개  |  진입확률>{args.entry_threshold}  |  "
        f"최대동시보유 {args.max_concurrent_positions}  |  "
        f"페이퍼 초기자본 {args.initial_capital_krw:,.0f}원\n"
        f"모델 학습기준일 {model.trained_at}"
    )

    scanner = MLSignalScanner(
        exchange=exchange,
        telegram=telegram,
        portfolio=portfolio,
        model=model,
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
