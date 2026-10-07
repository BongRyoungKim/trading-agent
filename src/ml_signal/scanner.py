"""
다변량 ML 신호(RandomForest) 페이퍼 스캐너 — 순수 로직(features.py,
model.py, exits.py)을 실제 거래소 데이터·페이퍼 포트폴리오와 연결하는
조립(wiring) 레이어. src/onchain/scanner.py·src/confluence/scanner.py와
동일한 설계 원칙(항상 PAPER, 라이브 엔진과 완전히 독립).

온체인/컨플루언스와 다른 점: 단일 종목이 아니라 여러 종목(기본 27개)을
매 스캔마다 순회하며, 동시 보유 슬롯 수를 제한한다(max_concurrent_
positions) — 1GB RAM VM에서 무제한 동시 포지션을 감당할 수 없기도 하고,
백테스트도 종목당 동일 명목금액(NOMINAL_POSITION)으로 분산 투자했으므로
소수 종목 쏠림 없는 분산 자체가 검증된 설계의 일부다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

from src.ml_signal.exits import MLSignalExitConfig, MLSignalPosition, check_exit
from src.ml_signal.features import latest_feature_row
from src.ml_signal.model import MLSignalModel
from src.portfolio.tracker import PortfolioTracker

if TYPE_CHECKING:
    from src.exchange.upbit import UpbitClient
    from src.utils.telegram import TelegramClient

# 2026-09-27 리서치(6구간 워크포워드 검증)에 쓰인 27개 종목과 정확히 동일 —
# 학습 데이터 유니버스와 실거래 스캔 유니버스가 어긋나면 안 됨.
DEFAULT_SYMBOLS = (
    "AAVE/KRW", "ADA/KRW", "ALGO/KRW", "ARB/KRW", "ATOM/KRW", "AVAX/KRW", "BCH/KRW",
    "BTC/KRW", "DOGE/KRW", "DOT/KRW", "ETH/KRW", "HBAR/KRW", "LINK/KRW", "NEAR/KRW",
    "ONDO/KRW", "PEPE/KRW", "POL/KRW", "SEI/KRW", "SHIB/KRW", "SOL/KRW", "SUI/KRW",
    "TRUMP/KRW", "TRX/KRW", "UNI/KRW", "VET/KRW", "XLM/KRW", "XRP/KRW",
)


@dataclass(frozen=True)
class MLSignalScannerConfig:
    """스캐너 운영 파라미터. 청산 파라미터는 MLSignalExitConfig가 담당."""

    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    position_size_krw: Decimal = Decimal("200000")   # 페이퍼 계좌 — 실제 자금 아님
    max_concurrent_positions: int = 5
    entry_threshold: float = 0.5   # 리서치(2026-09-27)에서 검증된 기본 임계값
    ohlcv_fetch_limit: int = 90    # MIN_BARS_REQUIRED(60)보다 여유있게 조회


def _bars_to_df(bars: list) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": [b.timestamp for b in bars],
        "open": [float(b.open) for b in bars],
        "high": [float(b.high) for b in bars],
        "low": [float(b.low) for b in bars],
        "close": [float(b.close) for b in bars],
        "volume": [float(b.volume) for b in bars],
    })


class MLSignalScanner:
    def __init__(
        self,
        exchange: "UpbitClient",
        telegram: "TelegramClient",
        portfolio: PortfolioTracker,
        model: MLSignalModel,
        exit_cfg: MLSignalExitConfig | None = None,
        scanner_cfg: MLSignalScannerConfig | None = None,
    ) -> None:
        self._exchange = exchange
        self._telegram = telegram
        self._portfolio = portfolio
        self._model = model
        self._exit_cfg = exit_cfg or MLSignalExitConfig()
        self._scanner_cfg = scanner_cfg or MLSignalScannerConfig()
        self._positions: dict[str, MLSignalPosition] = {}

    @property
    def open_symbols(self) -> list[str]:
        return list(self._positions.keys())

    # ── Exit checks (run every tick) ────────────────────────────────────────

    def check_exits(self) -> None:
        for symbol in list(self._positions.keys()):
            position = self._positions[symbol]
            try:
                ticker = self._exchange.get_ticker(symbol)
            except Exception as exc:  # noqa: BLE001 — one bad ticker must not kill the loop
                logger.warning("ML signal: ticker fetch failed during exit check", symbol=symbol, error=str(exc))
                continue

            signal = check_exit(position, ticker.last, cfg=self._exit_cfg)
            if signal is not None:
                self._close_position(symbol, signal.exit_price, signal.reason)

    def _close_position(self, symbol: str, exit_price: Decimal, reason: str) -> None:
        try:
            net_pnl = self._portfolio.close_position(symbol, exit_price)
        except Exception as exc:  # noqa: BLE001
            logger.error("ML signal: close_position failed", symbol=symbol, error=str(exc))
            return

        pos = self._positions.pop(symbol, None)
        pnl_pct = float((exit_price - pos.entry_price) / pos.entry_price * 100) if pos else 0.0
        logger.info(
            "ML signal position closed (PAPER)",
            symbol=symbol, reason=reason, net_pnl=float(net_pnl), pnl_pct=pnl_pct,
        )
        self._telegram.send_position_closed(
            symbol=symbol, entry_price=float(pos.entry_price), exit_price=float(exit_price),
            pnl=float(net_pnl), pnl_pct=pnl_pct,
        )
        reason_kr = {"stop_loss": "안전장치 손절", "time_stop": f"{self._exit_cfg.hold_days}일 보유기간 만료"}.get(reason, reason)
        self._telegram.send(f"🤖 ML신호 청산 [{reason_kr}] <code>{symbol}</code>")

    # ── Entry scan (run every scan_every_n_ticks) ───────────────────────────

    def refresh_signal_and_scan(self) -> None:
        """
        전 종목을 먼저 전부 스캔해 확률>entry_threshold인 후보를 모은 뒤,
        **확률이 높은 순으로** 남은 슬롯만큼만 진입한다.

        예전엔 cfg.symbols 순서(알파벳순)로 하나씩 확인하다 슬롯이 차면
        즉시 멈췄는데, 이러면 "그날 가장 자신있는 신호"가 아니라 "이름이
        앞쪽인 종목"이 우연히 선택되는 구조적 편향이 생겼다(2026-10-07
        발견 — AAVE/ADA/ALGO/ARB/AVAX가 매번 똑같이 뽑힌 원인). 백테스트는
        슬롯 경합 자체가 없었으므로(전 종목 동시 보유 가정), 실거래의
        슬롯 제한 안에서는 확률 랭킹으로 고르는 게 백테스트 의도에 더
        가깝다.
        """
        cfg = self._scanner_cfg
        available_slots = cfg.max_concurrent_positions - len(self._positions)
        if available_slots <= 0:
            logger.debug("ML signal: max concurrent positions reached, skipping scan")
            return

        candidates: list[tuple[float, str]] = []
        for symbol in cfg.symbols:
            if symbol in self._positions or self._portfolio.has_position(symbol):
                continue

            try:
                bars = self._exchange.get_ohlcv(symbol, "1d", limit=cfg.ohlcv_fetch_limit)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ML signal: OHLCV fetch failed", symbol=symbol, error=str(exc))
                continue

            row = latest_feature_row(_bars_to_df(bars))
            if row is None:
                continue

            proba = self._model.predict_proba(row)
            if proba > cfg.entry_threshold:
                candidates.append((proba, symbol))

        candidates.sort(key=lambda c: c[0], reverse=True)
        for proba, symbol in candidates[:available_slots]:
            self._open_position(symbol, proba)

    def _open_position(self, symbol: str, proba: float) -> None:
        try:
            ticker = self._exchange.get_ticker(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ML signal: ticker fetch failed, entry skipped", symbol=symbol, error=str(exc))
            return
        entry_price = ticker.last
        if entry_price <= 0:
            return
        amount = self._scanner_cfg.position_size_krw / entry_price

        try:
            self._portfolio.open_position(symbol=symbol, side="buy", amount=amount, entry_price=entry_price)
        except Exception as exc:  # noqa: BLE001
            logger.error("ML signal: open_position failed", symbol=symbol, error=str(exc))
            return

        position = MLSignalPosition.open_new(
            symbol=symbol, entry_price=entry_price, amount=amount, cfg=self._exit_cfg,
        )
        self._positions[symbol] = position

        logger.info(
            "ML signal position opened (PAPER)",
            symbol=symbol, entry_price=float(entry_price), proba=round(proba, 3),
            stop_loss=float(position.stop_loss),
        )
        self._telegram.send(
            "🤖 <b>ML신호 진입 (PAPER)</b>\n"
            f"<code>{symbol}</code>  진입 {float(entry_price):,.0f}\n"
            f"확률 {proba:.2f}  |  안전손절 {float(position.stop_loss):,.0f}"
        )

    # ── Main loop ────────────────────────────────────────────────────────────

    def run_forever(self, interval_seconds: int = 900, scan_every_n_ticks: int = 96) -> None:
        """
        interval_seconds마다 안전장치 손절 점검, scan_every_n_ticks번째
        틱마다 전 종목 신호 재계산+진입 스캔. 기본값(15분 x 96틱 = 24시간)은
        일봉 기반 신호(하루 단위로만 바뀜)와 맞춘 것 — onchain/confluence
        스캐너와 동일한 주기 설계 원칙.
        """
        logger.info(
            "ML signal scanner started (PAPER mode, independent of live engine)",
            interval_seconds=interval_seconds, scan_every_n_ticks=scan_every_n_ticks,
            symbols=len(self._scanner_cfg.symbols),
        )
        tick = 0
        while True:
            try:
                self.check_exits()
                if tick % scan_every_n_ticks == 0:
                    self.refresh_signal_and_scan()
            except Exception as exc:  # noqa: BLE001 — one bad tick must not kill the process
                logger.error("ML signal scanner tick failed", error=str(exc))
            tick += 1
            time.sleep(interval_seconds)


__all__ = ["MLSignalScanner", "MLSignalScannerConfig", "DEFAULT_SYMBOLS"]
