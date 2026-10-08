"""
VPIN(방향성 거래량 불균형) 페이퍼 스캐너 — 순수 로직(signal.py, exits.py,
binance_feed.py)을 실제 거래소 데이터·페이퍼 포트폴리오와 연결하는
조립(wiring) 레이어. src/ml_signal/scanner.py와 동일한 설계 원칙(항상
PAPER, 여러 종목 순회 + 슬롯 제한, 확률/강도 순 랭킹으로 알파벳순 편향
방지 — 2026-10-07 ml_signal에서 발견된 버그를 처음부터 피한다).

신호 자체(VPIN)는 바이낸스 테이커 매수/매도 데이터로 계산하지만, 포지션은
Upbit에서 체결/관리한다 — 종목 페어(symbol_pairs)로 둘을 묶는다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Callable

import pandas as pd
from loguru import logger

from src.vpin_signal.binance_feed import fetch_recent_klines
from src.vpin_signal.exits import VPINExitConfig, VPINPosition, check_exit
from src.vpin_signal.signal import MIN_BARS_REQUIRED, latest_row
from src.portfolio.tracker import PortfolioTracker

if TYPE_CHECKING:
    from src.exchange.upbit import UpbitClient
    from src.utils.telegram import TelegramClient

# 2026-10-08 리서치(scripts/vpin_funding_backtest.py)에서 검증된 12개 종목과
# 정확히 동일 — 바이낸스 USDT 페어 존재 확인됨.
DEFAULT_SYMBOL_PAIRS: tuple[tuple[str, str], ...] = (
    ("BTC/KRW", "BTCUSDT"), ("ETH/KRW", "ETHUSDT"), ("XRP/KRW", "XRPUSDT"),
    ("SOL/KRW", "SOLUSDT"), ("LINK/KRW", "LINKUSDT"), ("NEAR/KRW", "NEARUSDT"),
    ("SUI/KRW", "SUIUSDT"), ("UNI/KRW", "UNIUSDT"), ("BCH/KRW", "BCHUSDT"),
    ("ADA/KRW", "ADAUSDT"), ("DOGE/KRW", "DOGEUSDT"), ("TRX/KRW", "TRXUSDT"),
)


@dataclass(frozen=True)
class VPINScannerConfig:
    symbol_pairs: tuple[tuple[str, str], ...] = DEFAULT_SYMBOL_PAIRS
    position_size_krw: Decimal = Decimal("200000")   # 페이퍼 계좌 — 실제 자금 아님
    max_concurrent_positions: int = 5
    atr_period: int = 14
    binance_fetch_limit: int = 260  # MIN_BARS_REQUIRED(240)보다 여유있게 조회


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """리서치 스크립트(cvd_leadlag_backtest.py/_atr)와 동일한 단순 롤링평균
    ATR — src.utils.indicators.atr(Wilder 평활)와 다름을 의도적으로 유지한다
    (백테스트와 공식이 어긋나면 안 됨, 2026-10-08 리서치 재현성 우선)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


class VPINScanner:
    def __init__(
        self,
        exchange: "UpbitClient",
        telegram: "TelegramClient",
        portfolio: PortfolioTracker,
        exit_cfg: VPINExitConfig | None = None,
        scanner_cfg: VPINScannerConfig | None = None,
        binance_fetcher: Callable[[str, int], pd.DataFrame] = None,
    ) -> None:
        self._exchange = exchange
        self._telegram = telegram
        self._portfolio = portfolio
        self._exit_cfg = exit_cfg or VPINExitConfig()
        self._scanner_cfg = scanner_cfg or VPINScannerConfig()
        self._binance_fetcher = binance_fetcher or (
            lambda sym, limit: fetch_recent_klines(sym, limit=limit)
        )
        self._positions: dict[str, VPINPosition] = {}

    @property
    def open_symbols(self) -> list[str]:
        return list(self._positions.keys())

    # ── Exit checks (run every tick) ────────────────────────────────────────

    def check_exits(self) -> None:
        for symbol in list(self._positions.keys()):
            position = self._positions[symbol]
            try:
                ticker = self._exchange.get_ticker(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning("VPIN: ticker fetch failed during exit check", symbol=symbol, error=str(exc))
                continue

            signal = check_exit(position, ticker.last, cfg=self._exit_cfg)
            if signal is not None:
                self._close_position(symbol, signal.exit_price, signal.reason)

    def _close_position(self, symbol: str, exit_price: Decimal, reason: str) -> None:
        try:
            net_pnl = self._portfolio.close_position(symbol, exit_price)
        except Exception as exc:  # noqa: BLE001
            logger.error("VPIN: close_position failed", symbol=symbol, error=str(exc))
            return

        pos = self._positions.pop(symbol, None)
        pnl_pct = float((exit_price - pos.entry_price) / pos.entry_price * 100) if pos else 0.0
        logger.info(
            "VPIN position closed (PAPER)", symbol=symbol, reason=reason,
            net_pnl=float(net_pnl), pnl_pct=pnl_pct,
        )
        self._telegram.send_position_closed(
            symbol=symbol, entry_price=float(pos.entry_price), exit_price=float(exit_price),
            pnl=float(net_pnl), pnl_pct=pnl_pct,
        )
        reason_kr = {"stop_loss": "손절", "take_profit": "익절(RR목표)", "time_stop": "최대보유시간 초과"}.get(reason, reason)
        self._telegram.send(f"📊 VPIN신호 청산 [{reason_kr}] <code>{symbol}</code>")

    # ── Entry scan (run every tick -- 15분봉 신호라 매 틱 재계산) ───────────

    def refresh_signal_and_scan(self) -> None:
        cfg = self._scanner_cfg
        available_slots = cfg.max_concurrent_positions - len(self._positions)
        if available_slots <= 0:
            logger.debug("VPIN: max concurrent positions reached, skipping scan")
            return

        candidates: list[tuple[float, str]] = []  # (vpin값, symbol) -- 강도순 정렬용
        for upbit_sym, binance_sym in cfg.symbol_pairs:
            if upbit_sym in self._positions or self._portfolio.has_position(upbit_sym):
                continue
            try:
                bdf = self._binance_fetcher(binance_sym, cfg.binance_fetch_limit)
            except Exception as exc:  # noqa: BLE001
                logger.warning("VPIN: Binance fetch failed", symbol=binance_sym, error=str(exc))
                continue

            row = latest_row(bdf)
            if row is None:
                continue
            if row["vpin"] > row["vpin_threshold"] and row["net_delta_window"] > 0:
                candidates.append((float(row["vpin"]), upbit_sym))

        candidates.sort(key=lambda c: c[0], reverse=True)
        for vpin_value, symbol in candidates[:available_slots]:
            self._open_position(symbol, vpin_value)

    def _fetch_atr(self, symbol: str) -> float | None:
        limit = self._scanner_cfg.atr_period + 5
        try:
            bars = self._exchange.get_ohlcv(symbol, "15m", limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("VPIN: Upbit OHLCV fetch failed, cannot compute ATR", symbol=symbol, error=str(exc))
            return None
        if len(bars) < self._scanner_cfg.atr_period + 1:
            return None
        df = pd.DataFrame({
            "high": [float(b.high) for b in bars],
            "low": [float(b.low) for b in bars],
            "close": [float(b.close) for b in bars],
        })
        value = _atr(df, self._scanner_cfg.atr_period).iloc[-1]
        return float(value) if value == value else None  # NaN guard

    def _open_position(self, symbol: str, vpin_value: float) -> None:
        atr_value = self._fetch_atr(symbol)
        if atr_value is None:
            logger.warning("VPIN: skipping entry -- ATR unavailable", symbol=symbol)
            return

        try:
            ticker = self._exchange.get_ticker(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("VPIN: ticker fetch failed, entry skipped", symbol=symbol, error=str(exc))
            return
        entry_price = ticker.last
        if entry_price <= 0:
            return
        amount = self._scanner_cfg.position_size_krw / entry_price

        try:
            self._portfolio.open_position(symbol=symbol, side="buy", amount=amount, entry_price=entry_price)
        except Exception as exc:  # noqa: BLE001
            logger.error("VPIN: open_position failed", symbol=symbol, error=str(exc))
            return

        position = VPINPosition.open_new(
            symbol=symbol, entry_price=entry_price, amount=amount,
            atr_value=atr_value, cfg=self._exit_cfg,
        )
        self._positions[symbol] = position

        logger.info(
            "VPIN position opened (PAPER)", symbol=symbol, entry_price=float(entry_price),
            vpin=round(vpin_value, 4), stop_loss=float(position.stop_loss), take_profit=float(position.take_profit),
        )
        self._telegram.send(
            "📊 <b>VPIN신호 진입 (PAPER)</b>\n"
            f"<code>{symbol}</code>  진입 {float(entry_price):,.0f}\n"
            f"VPIN {vpin_value:.3f}  |  손절 {float(position.stop_loss):,.0f}  |  익절 {float(position.take_profit):,.0f}"
        )

    # ── Main loop ────────────────────────────────────────────────────────────

    def run_forever(self, interval_seconds: int = 900) -> None:
        """15분(interval_seconds)마다 손절/익절/타임스탑 점검과 신호
        재계산+진입 스캔을 **매 틱 모두** 수행한다 — 신호 자체가 15분봉
        단위로 바뀌는 리서치 설계와 맞춘 것(onchain/confluence와 달리
        '손절은 자주, 신호는 가끔' 이원화가 필요 없음 — 이미 가장 빠른
        주기)."""
        logger.info(
            "VPIN scanner started (PAPER mode, independent of live engine)",
            interval_seconds=interval_seconds, symbols=len(self._scanner_cfg.symbol_pairs),
        )
        while True:
            try:
                self.check_exits()
                self.refresh_signal_and_scan()
            except Exception as exc:  # noqa: BLE001 — one bad tick must not kill the process
                logger.error("VPIN scanner tick failed", error=str(exc))
            time.sleep(interval_seconds)


__all__ = ["VPINScanner", "VPINScannerConfig", "DEFAULT_SYMBOL_PAIRS"]
