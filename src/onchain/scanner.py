"""
BTC 온체인 넷플로우 페이퍼 스캐너 — 순수 로직(netflow.py, exits.py)을 실제
거래소 데이터·페이퍼 포트폴리오와 연결하는 조립(wiring) 레이어.

항상 페이퍼 모드로만 동작한다 (라이브 자본·라이브 엔진과 완전히 독립) —
src/breakout/scanner.py와 동일한 설계 원칙.

이원화된 주기(계획서 "실행 주기" 섹션):
  - 매 틱(짧은 주기, 기본 15분): check_exits()로 손절가만 확인 — 신호가
    바뀌지 않아도 급락에 무방비로 노출되지 않도록.
  - scan_every_n_ticks번째 틱마다(기본 96틱=15분x96=24시간): 넷플로우
    재조회 + z-score 재계산 + (포지션 없으면) 진입 판정. 이 신호 자체는
    백테스트와 동일하게 일 단위로만 바뀐다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

from src.onchain.exits import (
    OnchainExitConfig,
    OnchainPosition,
    check_exit,
)
from src.onchain.netflow import DEFAULT_LOOKBACK_DAYS, compute_zscore, fetch_netflow_history
from src.portfolio.tracker import PortfolioTracker
from src.utils.indicators import atr as calc_atr

if TYPE_CHECKING:
    from src.exchange.upbit import UpbitClient
    from src.utils.telegram import TelegramClient


@dataclass(frozen=True)
class OnchainScannerConfig:
    """스캐너 운영 파라미터. 청산 파라미터는 OnchainExitConfig가 담당."""

    symbol: str = "BTC/KRW"
    position_size_krw: Decimal = Decimal("100000")   # 페이퍼 계좌 — 실제 자금 아님
    entry_z_threshold: float = -1.5
    zscore_lookback_days: int = DEFAULT_LOOKBACK_DAYS
    netflow_history_days: int = 60   # zscore_lookback_days보다 여유있게 조회
    atr_period: int = 14


class OnchainScanner:
    def __init__(
        self,
        exchange: "UpbitClient",
        telegram: "TelegramClient",
        portfolio: PortfolioTracker,
        exit_cfg: OnchainExitConfig | None = None,
        scanner_cfg: OnchainScannerConfig | None = None,
    ) -> None:
        self._exchange = exchange
        self._telegram = telegram
        self._portfolio = portfolio
        self._exit_cfg = exit_cfg or OnchainExitConfig()
        self._scanner_cfg = scanner_cfg or OnchainScannerConfig()
        self._position: OnchainPosition | None = None
        self._latest_zscore: float | None = None

    @property
    def has_position(self) -> bool:
        return self._position is not None

    # ── Exit checks (run every tick) ────────────────────────────────────────

    def check_exits(self) -> None:
        if self._position is None:
            return
        symbol = self._scanner_cfg.symbol
        try:
            ticker = self._exchange.get_ticker(symbol)
        except Exception as exc:  # noqa: BLE001 — one bad ticker must not kill the loop
            logger.warning("Onchain: ticker fetch failed during exit check", symbol=symbol, error=str(exc))
            return

        signal = check_exit(
            self._position, ticker.last, self._latest_zscore, cfg=self._exit_cfg,
        )
        if signal is not None:
            self._close_position(signal.exit_price, signal.reason)

    def _close_position(self, exit_price: Decimal, reason: str) -> None:
        symbol = self._scanner_cfg.symbol
        try:
            net_pnl = self._portfolio.close_position(symbol, exit_price)
        except Exception as exc:  # noqa: BLE001
            logger.error("Onchain: close_position failed", symbol=symbol, error=str(exc))
            return

        pos = self._position
        self._position = None
        pnl_pct = float((exit_price - pos.entry_price) / pos.entry_price * 100) if pos else 0.0
        logger.info(
            "Onchain position closed (PAPER)",
            symbol=symbol, reason=reason, net_pnl=float(net_pnl), pnl_pct=pnl_pct,
        )
        self._telegram.send_position_closed(
            symbol=symbol, entry_price=float(pos.entry_price), exit_price=float(exit_price),
            pnl=float(net_pnl), pnl_pct=pnl_pct,
        )
        reason_kr = {"stop_loss": "손절", "signal": "신호반전(넷플로우 회복)", "time_stop": "최대보유일 초과"}.get(reason, reason)
        self._telegram.send(f"🔗 온체인 넷플로우 청산 [{reason_kr}] <code>{symbol}</code>")

    # ── Entry scan (run every scan_every_n_ticks) ───────────────────────────

    def refresh_signal_and_scan(self) -> None:
        try:
            df = fetch_netflow_history(days=self._scanner_cfg.netflow_history_days)
        except Exception as exc:  # noqa: BLE001 — 무료 API라 다운타임 전제, 이전 z-score 유지
            logger.warning("Onchain: netflow fetch failed, keeping previous z-score", error=str(exc))
            return

        z = compute_zscore(df, lookback=self._scanner_cfg.zscore_lookback_days)
        if z is None:
            logger.debug("Onchain: insufficient netflow history for z-score yet")
            return
        self._latest_zscore = z
        logger.info("Onchain: netflow z-score updated", zscore=round(z, 3))

        if self._position is not None:
            return  # 이미 보유 중 — 신규 진입 스캔 안 함
        if self._portfolio.has_position(self._scanner_cfg.symbol):
            return  # 다른 경로로 이미 포지션이 있으면(예: 재시작 후 복원) 진입 skip

        if z <= self._scanner_cfg.entry_z_threshold:
            self._open_position(z)

    def _fetch_atr(self) -> float | None:
        limit = self._scanner_cfg.atr_period + 5
        try:
            bars = self._exchange.get_ohlcv(self._scanner_cfg.symbol, "1d", limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Onchain: daily OHLCV fetch failed, cannot compute ATR", error=str(exc))
            return None
        if len(bars) < self._scanner_cfg.atr_period + 1:
            return None
        df = pd.DataFrame({
            "high": [float(b.high) for b in bars],
            "low": [float(b.low) for b in bars],
            "close": [float(b.close) for b in bars],
        })
        atr_series = calc_atr(df["high"], df["low"], df["close"], self._scanner_cfg.atr_period)
        value = atr_series.iloc[-1]
        return float(value) if value == value else None  # NaN guard

    def _open_position(self, zscore: float) -> None:
        symbol = self._scanner_cfg.symbol
        atr_value = self._fetch_atr()
        if atr_value is None:
            logger.warning("Onchain: skipping entry — ATR unavailable", symbol=symbol)
            return

        try:
            ticker = self._exchange.get_ticker(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Onchain: ticker fetch failed, entry skipped", symbol=symbol, error=str(exc))
            return
        entry_price = ticker.last
        if entry_price <= 0:
            return
        amount = self._scanner_cfg.position_size_krw / entry_price

        try:
            self._portfolio.open_position(symbol=symbol, side="buy", amount=amount, entry_price=entry_price)
        except Exception as exc:  # noqa: BLE001
            logger.error("Onchain: open_position failed", symbol=symbol, error=str(exc))
            return

        self._position = OnchainPosition.open_new(
            symbol=symbol, entry_price=entry_price, amount=amount,
            atr_value=atr_value, cfg=self._exit_cfg,
        )

        logger.info(
            "Onchain position opened (PAPER)",
            symbol=symbol, entry_price=float(entry_price), zscore=round(zscore, 3),
            stop_loss=float(self._position.stop_loss),
        )
        self._telegram.send(
            "🔗 <b>온체인 넷플로우 진입 (PAPER)</b>\n"
            f"<code>{symbol}</code>  진입 {float(entry_price):,.0f}\n"
            f"z-score {zscore:.2f}  |  손절 {float(self._position.stop_loss):,.0f}"
        )

    # ── Main loop ────────────────────────────────────────────────────────────

    def run_forever(self, interval_seconds: int = 900, scan_every_n_ticks: int = 96) -> None:
        """
        interval_seconds마다 손절 점검, scan_every_n_ticks번째 틱마다 넷플로우
        재조회+진입판정. 기본값(15분 x 96틱 = 24시간)은 신호 자체가 일 단위로만
        바뀌는 백테스트 설계와 맞추되, 손절만은 더 자주 확인(계획서 "실행 주기"
        섹션 — 백테스트에 없던 안전장치를 실거래에 의도적으로 추가).
        """
        logger.info(
            "Onchain netflow scanner started (PAPER mode, independent of live engine)",
            interval_seconds=interval_seconds, scan_every_n_ticks=scan_every_n_ticks,
        )
        tick = 0
        while True:
            try:
                self.check_exits()
                if tick % scan_every_n_ticks == 0:
                    self.refresh_signal_and_scan()
            except Exception as exc:  # noqa: BLE001 — one bad tick must not kill the process
                logger.error("Onchain scanner tick failed", error=str(exc))
            tick += 1
            time.sleep(interval_seconds)


__all__ = ["OnchainScanner", "OnchainScannerConfig"]
