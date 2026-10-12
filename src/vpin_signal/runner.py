"""
VPIN(방향성 거래량 불균형) 신호 스캐너 프로세스 진입점
(`python -m src.vpin_signal.runner`).

라이브 엔진과 완전히 독립된 프로세스 — 별도 docker-compose 서비스
(vpin-signal), onchain-btc/confluence-btc/ml-signal과 동일 설계 원칙
(항상 PAPER, 실제 자금 접근 없음).

CLAUDE.md 하드게이트(실매매 전 페이퍼 검증 필수)에 따라 이번 라운드도
페이퍼 전용. 근거: 2026-10-08 리서치 — 12개 종목, 8개월을 6등분한 구간별
분해에서 6구간 중 4구간 플러스(최근 구간 특히 강함, Sharpe 최대 +5.06).
다만 train 구간 다수가 약하거나 마이너스였고 최근 구간에서 유독 강한
패턴이라 "최근 국면 한정 효과"일 가능성을 배제 못함 — 페이퍼로 먼저
확인한다. 이 신호는 최대 보유 20시간(다른 페이퍼 전략보다 훨씬 빠른
회전)이라 통계적으로 유의미한 표본(약 20~30건)에 1~2주면 도달할 것으로
예상 — 사용자 요청으로 관찰기간을 "거래건수 기준"으로 짧게 잡는다.
"""
from __future__ import annotations

import argparse
from decimal import Decimal

from src.config.settings import get_settings
from src.exchange.upbit import UpbitClient
from src.portfolio.tracker import PortfolioTracker
from src.risk.manager import PortfolioState, RiskManager
from src.utils.logger import logger, setup_logger
from src.utils.telegram import TelegramClient
from src.vpin_signal.exits import VPINExitConfig
from src.vpin_signal.scanner import DEFAULT_SYMBOL_PAIRS, VPINScanner, VPINScannerConfig


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="VPIN 방향성 거래량 불균형 페이퍼 스캐너 (라이브 엔진과 독립 프로세스)"
    )
    parser.add_argument(
        "--interval-seconds", type=int, default=900,
        help="틱(손절/익절/타임스탑 점검 + 신호 재계산) 주기(초). 기본 900초(15분, 신호 자체 봉 간격과 일치)",
    )
    parser.add_argument("--initial-capital-krw", type=float, default=1_000_000.0)
    parser.add_argument("--position-size-krw", type=float, default=200_000.0)
    parser.add_argument("--max-concurrent-positions", type=int, default=5)
    parser.add_argument("--atr-multiplier", type=float, default=2.5)
    parser.add_argument("--sl-ceiling-pct", type=float, default=1.5)
    parser.add_argument("--sl-floor-pct", type=float, default=5.0)
    parser.add_argument("--tp-rr-multiplier", type=float, default=1.5)
    parser.add_argument("--max-hold-hours", type=float, default=20.0)
    parser.add_argument(
        "--consecutive-loss-limit", type=int, default=3,
        help="이 횟수만큼 연속 손실 시 신규 진입 일시중단 (라이브 15분봉 엔진 기본값과 동일). 0=비활성",
    )
    parser.add_argument(
        "--consecutive-loss-cooldown-min", type=int, default=720,
        help="연속손실 쿨다운 지속 시간(분). 기본 720분(12시간)",
    )
    parser.add_argument(
        "--disable-risk-manager", action="store_true",
        help="MDD강제정지/일일손실한도/연속손실쿨다운 전부 비활성화(기존 배포 버전과 동일하게 동작) "
             "-- 안전장치 자체를 끄는 옵션이라 기본값은 False(항상 켜짐)",
    )
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

    exit_cfg = VPINExitConfig(
        atr_multiplier=args.atr_multiplier,
        sl_ceiling_pct=args.sl_ceiling_pct,
        sl_floor_pct=args.sl_floor_pct,
        tp_rr_multiplier=args.tp_rr_multiplier,
        max_hold_hours=args.max_hold_hours,
    )
    scanner_cfg = VPINScannerConfig(
        symbol_pairs=DEFAULT_SYMBOL_PAIRS,
        position_size_krw=Decimal(str(args.position_size_krw)),
        max_concurrent_positions=args.max_concurrent_positions,
    )

    # 2026-10-12 추가 — 실거래 전환 전 필수 안전장치(사용자 요청). 라이브
    # 15분봉 엔진과 동일한 RiskManager를 그대로 재사용 — MDD강제정지/
    # 일일손실한도 임계값은 settings(.env)의 전역값을 그대로 쓴다(별도
    # VPIN 전용값을 아직 정하지 않았으므로 라이브와 같은 기준 적용).
    risk_manager = None
    if not args.disable_risk_manager:
        initial_capital = Decimal(str(args.initial_capital_krw))
        risk_state = PortfolioState(capital=initial_capital, peak_capital=initial_capital)
        risk_manager = RiskManager(
            settings, risk_state,
            consecutive_loss_limit=args.consecutive_loss_limit,
            consecutive_loss_cooldown_minutes=args.consecutive_loss_cooldown_min,
        )

    logger.info(
        "VPIN scanner starting (PAPER mode, independent process)",
        symbols=len(scanner_cfg.symbol_pairs),
        initial_capital_krw=args.initial_capital_krw,
        max_concurrent_positions=args.max_concurrent_positions,
        risk_manager_enabled=risk_manager is not None,
    )
    telegram.send(
        "📊 <b>VPIN 방향성 거래량 신호 스캐너 시작</b> (PAPER, 라이브 봇과 독립 프로세스)\n"
        f"종목 {len(scanner_cfg.symbol_pairs)}개  |  최대동시보유 {args.max_concurrent_positions}  |  "
        f"최대보유시간 {args.max_hold_hours}시간  |  페이퍼 초기자본 {args.initial_capital_krw:,.0f}원\n"
        f"안전장치: {'MDD ' + str(int(settings.max_drawdown_halt*100)) + '% / 일일손실 ' + str(int(settings.max_daily_loss*100)) + '% / 연속손실 ' + str(args.consecutive_loss_limit) + '회 쿨다운 적용' if risk_manager else '비활성(--disable-risk-manager)'}"
    )

    scanner = VPINScanner(
        exchange=exchange, telegram=telegram, portfolio=portfolio,
        exit_cfg=exit_cfg, scanner_cfg=scanner_cfg, risk_manager=risk_manager,
    )
    scanner.run_forever(interval_seconds=args.interval_seconds)
    return 0  # pragma: no cover — run_forever loops forever; unreachable in practice


if __name__ == "__main__":
    raise SystemExit(main())
