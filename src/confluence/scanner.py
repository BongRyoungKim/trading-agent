"""
1d+4h+1h 컨플루언스 BTC 페이퍼 스캐너 — 순수 로직(htf_regime.py,
exits.py)을 실제 거래소 데이터·페이퍼 포트폴리오와 연결하는 조립
(wiring) 레이어. 항상 페이퍼 모드로만 동작(src/breakout/scanner.py,
src/onchain/scanner.py와 동일 설계 원칙).

이원화된 주기: 1d/4h 국면은 자주 안 바뀌므로(4시간에 한 번꼴) 매 틱마다
다시 조회하면 API만 낭비 — refresh_signal_and_scan()에서만 재계산하고
결과를 캐싱, check_exits()는 그 캐시값 + 매 틱 최신가로 손절만 확인.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

from src.confluence.exits import ConfluenceExitConfig, ConfluencePosition, check_exit, update_trailing
from src.confluence.htf_regime import is_uptrend

if TYPE_CHECKING:
    from src.exchange.upbit import UpbitClient
    from src.utils.telegram import TelegramClient


@dataclass(frozen=True)
class ConfluenceScannerConfig:
    symbol: str = "BTC/KRW"
    position_size_krw: Decimal = Decimal("100000")   # 페이퍼 계좌 — 실제 자금 아님
    breakout_window: int = 8       # 1시간봉x8=8시간 신고종가
    vol_mult: float = 1.5


def _to_df(bars) -> pd.DataFrame:
    return pd.DataFrame({
        "open": [float(b.open) for b in bars],
        "high": [float(b.high) for b in bars],
        "low": [float(b.low) for b in bars],
        "close": [float(b.close) for b in bars],
        "volume": [float(b.volume) for b in bars],
    })


class ConfluenceScanner:
    def __init__(
        self,
        exchange: "UpbitClient",
        telegram: "TelegramClient",
        portfolio,
        exit_cfg: ConfluenceExitConfig | None = None,
        scanner_cfg: ConfluenceScannerConfig | None = None,
    ) -> None:
        self._exchange = exchange
        self._telegram = telegram
        self._portfolio = portfolio
        self._exit_cfg = exit_cfg or ConfluenceExitConfig()
        self._scanner_cfg = scanner_cfg or ConfluenceScannerConfig()
        self._position: ConfluencePosition | None = None
        self._latest_confluence_ok: bool = False

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
        except Exception as exc:  # noqa: BLE001
            logger.warning("Confluence: ticker fetch failed during exit check", symbol=symbol, error=str(exc))
            return

        signal = check_exit(self._position, ticker.last, self._latest_confluence_ok, cfg=self._exit_cfg)
        if signal is not None:
            self._close_position(signal.exit_price, signal.reason)
            return

        updated = update_trailing(self._position, ticker.last, cfg=self._exit_cfg)
        if updated.stop_loss != self._position.stop_loss:
            self._sync_stop_loss(symbol, updated.stop_loss)
        self._position = updated

    def _sync_stop_loss(self, symbol: str, new_stop: Decimal) -> None:
        try:
            self._portfolio.update_stop_loss(symbol, new_stop)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Confluence: stop-loss sync failed", symbol=symbol, error=str(exc))

    def _close_position(self, exit_price: Decimal, reason: str) -> None:
        symbol = self._scanner_cfg.symbol
        try:
            net_pnl = self._portfolio.close_position(symbol, exit_price)
        except Exception as exc:  # noqa: BLE001
            logger.error("Confluence: close_position failed", symbol=symbol, error=str(exc))
            return

        pos = self._position
        self._position = None
        pnl_pct = float((exit_price - pos.entry_price) / pos.entry_price * 100) if pos else 0.0
        logger.info(
            "Confluence position closed (PAPER)",
            symbol=symbol, reason=reason, net_pnl=float(net_pnl), pnl_pct=pnl_pct,
        )
        self._telegram.send_position_closed(
            symbol=symbol, entry_price=float(pos.entry_price), exit_price=float(exit_price),
            pnl=float(net_pnl), pnl_pct=pnl_pct,
        )
        reason_kr = {
            "stop_loss": "손절", "htf_trend_break": "상위국면(1d/4h) 이탈", "time_stop": "최대보유시간 초과",
        }.get(reason, reason)
        self._telegram.send(f"📈 컨플루언스 청산 [{reason_kr}] <code>{symbol}</code>")

    # ── Entry scan + 국면 갱신 (run every scan_every_n_ticks) ────────────────

    def refresh_signal_and_scan(self) -> None:
        symbol = self._scanner_cfg.symbol
        try:
            daily_bars = self._exchange.get_ohlcv(symbol, "1d", limit=60)
            h4_bars = self._exchange.get_ohlcv(symbol, "4h", limit=60)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Confluence: HTF OHLCV fetch failed, keeping previous confluence state", error=str(exc))
            return

        daily_ok = is_uptrend(_to_df(daily_bars))
        h4_ok = is_uptrend(_to_df(h4_bars))
        self._latest_confluence_ok = daily_ok and h4_ok
        logger.info(
            "Confluence: HTF regime updated",
            daily_uptrend=daily_ok, h4_uptrend=h4_ok, confluence_ok=self._latest_confluence_ok,
        )

        if self._position is not None:
            return
        if self._portfolio.has_position(symbol):
            return
        if not self._latest_confluence_ok:
            return

        try:
            h1_bars = self._exchange.get_ohlcv(
                symbol, "1h", limit=self._scanner_cfg.breakout_window + 25,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Confluence: 1h OHLCV fetch failed, entry skipped", error=str(exc))
            return
        df1h = _to_df(h1_bars)
        if len(df1h) < self._scanner_cfg.breakout_window + 21:
            return

        breakout_level = df1h["close"].iloc[-self._scanner_cfg.breakout_window - 1:-1].max()
        vol_avg = df1h["volume"].iloc[-21:-1].mean()
        current = df1h.iloc[-1]
        is_breakout = current["close"] > breakout_level
        vol_ok = vol_avg > 0 and current["volume"] >= self._scanner_cfg.vol_mult * vol_avg

        if is_breakout and vol_ok:
            self._open_position()

    def _open_position(self) -> None:
        symbol = self._scanner_cfg.symbol
        try:
            ticker = self._exchange.get_ticker(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Confluence: ticker fetch failed, entry skipped", symbol=symbol, error=str(exc))
            return
        entry_price = ticker.last
        if entry_price <= 0:
            return
        amount = self._scanner_cfg.position_size_krw / entry_price

        try:
            self._portfolio.open_position(symbol=symbol, side="buy", amount=amount, entry_price=entry_price)
        except Exception as exc:  # noqa: BLE001
            logger.error("Confluence: open_position failed", symbol=symbol, error=str(exc))
            return

        self._position = ConfluencePosition.open_new(
            symbol=symbol, entry_price=entry_price, amount=amount, cfg=self._exit_cfg,
        )
        logger.info(
            "Confluence position opened (PAPER)",
            symbol=symbol, entry_price=float(entry_price), stop_loss=float(self._position.stop_loss),
        )
        self._telegram.send(
            "📈 <b>1d+4h+1h 컨플루언스 진입 (PAPER)</b>\n"
            f"<code>{symbol}</code>  진입 {float(entry_price):,.0f}\n"
            f"손절 {float(self._position.stop_loss):,.0f}"
        )

    # ── Main loop ────────────────────────────────────────────────────────────

    def run_forever(self, interval_seconds: int = 300, scan_every_n_ticks: int = 12) -> None:
        """기본값(5분 x 12틱 = 1시간)은 손절은 자주(5분), 국면갱신+진입판정은
        신호 자체의 자연 주기인 1시간마다 수행하도록 맞춘 것."""
        logger.info(
            "Confluence scanner started (PAPER mode, independent of live engine)",
            interval_seconds=interval_seconds, scan_every_n_ticks=scan_every_n_ticks,
        )
        tick = 0
        while True:
            try:
                self.check_exits()
                if tick % scan_every_n_ticks == 0:
                    self.refresh_signal_and_scan()
            except Exception as exc:  # noqa: BLE001 — one bad tick must not kill the process
                logger.error("Confluence scanner tick failed", error=str(exc))
            tick += 1
            time.sleep(interval_seconds)


__all__ = ["ConfluenceScanner", "ConfluenceScannerConfig"]
