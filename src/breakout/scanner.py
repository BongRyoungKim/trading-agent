"""
브레이크아웃 조기진입 스캐너 — 순수 로직(detector.py, exits.py)을 실제
거래소 데이터 및 페이퍼 포트폴리오와 연결하는 조립(wiring) 레이어.

항상 페이퍼 모드로만 동작한다 (라이브 자본·라이브 엔진과 완전히 독립).

2단계 스캔:
  1) exchange.get_liquid_symbols()로 유동성 하한을 넘는 후보 심볼 목록을
     한 번의 fetch_tickers() 호출로 저렴하게 확보.
  2) 후보별로 5분봉 OHLCV를 조회해 detect_breakout()으로 순수 판별.

보유 페이퍼 포지션은 매 틱(청산 점검 주기)마다 check_exit()을 먼저 확인해
신규 진입 스캔보다 우선 처리한다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

from src.breakout.detector import BreakoutConfig, BreakoutSignal, detect_breakout
from src.breakout.exits import BreakoutPosition, ExitConfig, check_exit, update_trailing
from src.portfolio.tracker import PortfolioTracker

if TYPE_CHECKING:
    from src.exchange.upbit import UpbitClient
    from src.utils.telegram import TelegramClient


@dataclass(frozen=True)
class ScannerConfig:
    """스캐너 운영 파라미터. 탐지·청산 파라미터는 각각 BreakoutConfig/ExitConfig가 담당."""

    max_concurrent_positions: int = 3
    position_size_krw: Decimal = Decimal("100000")   # 페이퍼 계좌 — 실제 자금 아님
    timeframe: str = "5m"
    ohlcv_limit_buffer: int = 5   # base_window_bars 위에 얹는 여유분


class BreakoutScanner:
    def __init__(
        self,
        exchange: "UpbitClient",
        telegram: "TelegramClient",
        portfolio: PortfolioTracker,
        detector_cfg: BreakoutConfig | None = None,
        exit_cfg: ExitConfig | None = None,
        scanner_cfg: ScannerConfig | None = None,
    ) -> None:
        self._exchange = exchange
        self._telegram = telegram
        self._portfolio = portfolio
        self._detector_cfg = detector_cfg or BreakoutConfig()
        self._exit_cfg = exit_cfg or ExitConfig()
        self._scanner_cfg = scanner_cfg or ScannerConfig()
        self._breakout_positions: dict[str, BreakoutPosition] = {}

    @property
    def open_symbols(self) -> list[str]:
        return list(self._breakout_positions.keys())

    # ── Exit checks (run every tick) ────────────────────────────────────────

    def check_exits(self) -> None:
        for symbol in list(self._breakout_positions.keys()):
            bpos = self._breakout_positions[symbol]
            try:
                ticker = self._exchange.get_ticker(symbol)
            except Exception as exc:  # noqa: BLE001 — one bad ticker must not kill the loop
                logger.warning(
                    "Breakout: ticker fetch failed during exit check",
                    symbol=symbol,
                    error=str(exc),
                )
                continue
            current_price = ticker.last

            signal = check_exit(bpos, current_price, cfg=self._exit_cfg)
            if signal is not None:
                self._close_position(symbol, signal.exit_price, signal.reason)
                continue

            updated = update_trailing(bpos, current_price, cfg=self._exit_cfg)
            self._breakout_positions[symbol] = updated
            if updated.stop_loss != bpos.stop_loss:
                self._sync_stop_loss(symbol, updated.stop_loss)

    def _sync_stop_loss(self, symbol: str, new_stop: Decimal) -> None:
        try:
            self._portfolio.update_stop_loss(symbol, new_stop)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Breakout: stop-loss sync failed", symbol=symbol, error=str(exc))

    def _close_position(self, symbol: str, exit_price: Decimal, reason: str) -> None:
        bpos = self._breakout_positions.pop(symbol, None)
        if bpos is None:
            return
        try:
            net_pnl = self._portfolio.close_position(symbol, exit_price)
        except Exception as exc:  # noqa: BLE001
            logger.error("Breakout: close_position failed", symbol=symbol, error=str(exc))
            return

        pnl_pct = float((exit_price - bpos.entry_price) / bpos.entry_price * 100)
        logger.info(
            "Breakout position closed (PAPER)",
            symbol=symbol,
            reason=reason,
            net_pnl=float(net_pnl),
            pnl_pct=pnl_pct,
        )
        self._telegram.send_position_closed(
            symbol=symbol,
            entry_price=float(bpos.entry_price),
            exit_price=float(exit_price),
            pnl=float(net_pnl),
            pnl_pct=pnl_pct,
        )
        reason_kr = {"stop_loss": "손절/트레일링", "time_stop": "타임스탑"}.get(reason, reason)
        self._telegram.send(f"🎯 브레이크아웃 청산 [{reason_kr}] <code>{symbol}</code>")

    # ── Entry scan ───────────────────────────────────────────────────────────

    def scan_for_entries(self) -> None:
        if len(self._breakout_positions) >= self._scanner_cfg.max_concurrent_positions:
            logger.debug("Breakout: max concurrent positions reached, skipping scan")
            return

        try:
            candidates = self._exchange.get_liquid_symbols(self._detector_cfg.min_quote_volume_krw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Breakout: candidate scan failed", error=str(exc))
            return

        checked = 0
        opened = 0
        for symbol in candidates:
            if len(self._breakout_positions) >= self._scanner_cfg.max_concurrent_positions:
                break
            if symbol in self._breakout_positions or self._portfolio.has_position(symbol):
                continue

            df = self._fetch_ohlcv_df(symbol)
            if df is None:
                continue
            checked += 1

            signal = detect_breakout(df, symbol, self._detector_cfg)
            if signal is None:
                continue

            self._open_position(symbol, signal)
            opened += 1

        # 스캔 사이클마다(기본 5분) 한 번씩 남기는 요약 로그 — 프로세스가 조용히
        # 죽어있는 건지 "신호가 없어서 조용한" 정상 상태인지 로그만으로 구분 가능하게.
        logger.info(
            "Breakout scan cycle complete",
            candidates_liquid=len(candidates),
            candidates_checked=checked,
            positions_opened_this_cycle=opened,
            open_positions=len(self._breakout_positions),
        )

    def _fetch_ohlcv_df(self, symbol: str) -> pd.DataFrame | None:
        limit = self._detector_cfg.base_window_bars + self._scanner_cfg.ohlcv_limit_buffer
        try:
            bars = self._exchange.get_ohlcv(symbol, self._scanner_cfg.timeframe, limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Breakout: OHLCV fetch failed", symbol=symbol, error=str(exc))
            return None
        if not bars:
            return None
        return pd.DataFrame(
            {
                "open": [float(b.open) for b in bars],
                "high": [float(b.high) for b in bars],
                "low": [float(b.low) for b in bars],
                "close": [float(b.close) for b in bars],
                "volume": [float(b.volume) for b in bars],
            }
        )

    def _open_position(self, symbol: str, signal: BreakoutSignal) -> None:
        entry_price = Decimal(str(signal.breakout_price))
        if entry_price <= 0:
            return
        amount = self._scanner_cfg.position_size_krw / entry_price

        try:
            self._portfolio.open_position(
                symbol=symbol,
                side="buy",
                amount=amount,
                entry_price=entry_price,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Breakout: open_position failed", symbol=symbol, error=str(exc))
            return

        bpos = BreakoutPosition.open_new(
            symbol=symbol,
            entry_price=entry_price,
            amount=amount,
            base_high=Decimal(str(signal.base_high)),
            cfg=self._exit_cfg,
        )
        self._breakout_positions[symbol] = bpos
        self._sync_stop_loss(symbol, bpos.stop_loss)

        logger.info(
            "Breakout position opened (PAPER)",
            symbol=symbol,
            entry_price=float(entry_price),
            base_high=signal.base_high,
            volume_ratio=signal.volume_ratio,
            move_from_base_low_pct=signal.move_from_base_low_pct,
        )
        self._telegram.send(
            "🚀 <b>브레이크아웃 조기진입 (PAPER)</b>\n"
            f"<code>{symbol}</code>  진입 {float(entry_price):,.2f}\n"
            f"바닥고점 {signal.base_high:,.2f}  |  거래량 {signal.volume_ratio:.1f}배  |  "
            f"저점대비 +{signal.move_from_base_low_pct:.1f}%"
        )

    # ── Main loop ────────────────────────────────────────────────────────────

    def run_forever(self, interval_seconds: int = 60, scan_every_n_ticks: int = 5) -> None:
        """
        interval_seconds마다 청산 점검, scan_every_n_ticks번째 틱마다 신규
        진입 스캔. 기본값(60초 x 5틱 = 5분)은 5분봉 기준 봉 하나가 완성되는
        주기와 맞춘 것.
        """
        logger.info(
            "Breakout scanner started (PAPER mode, independent of live engine)",
            interval_seconds=interval_seconds,
            scan_every_n_ticks=scan_every_n_ticks,
        )
        tick = 0
        while True:
            try:
                self.check_exits()
                if tick % scan_every_n_ticks == 0:
                    self.scan_for_entries()
            except Exception as exc:  # noqa: BLE001 — one bad tick must not kill the process
                logger.error("Breakout scanner tick failed", error=str(exc))
            tick += 1
            time.sleep(interval_seconds)


__all__ = ["BreakoutScanner", "ScannerConfig"]
