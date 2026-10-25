"""
VPIN(방향성 거래량 불균형) 신호 — 과거 데이터 기반 백테스트(파라미터 스윕).

2026-10-08 리서치 스크립트(scripts/vpin_funding_backtest.py, 이전 세션의
스크래치 작업물 — 저장소에 남아있지 않음)를 대체한다. VPIN/순델타 공식,
청산 로직, 포지션 사이징/슬롯 규칙은 재구현하지 않고 실거래 경로의
실제 코드를 그대로 가져다 쓴다:

  - src.vpin_signal.signal.compute_vpin_features() — VPIN/임계값/순델타
  - src.vpin_signal.exits.check_exit()/VPINPosition.open_new() — 손절/익절/타임스탑
  - src.vpin_signal.scanner._atr() — ATR(단순 롤링평균, Upbit 가격)
  - src.portfolio.tracker.PortfolioTracker — 현금/포지션 회계
  - src.backtest.data_split.split_time_ordered() — 시간순 70/15/15 분할

용도: 신호(vpin_threshold의 quantile, net_delta_window 조건)를 더 보수적으로
바꾸면 수익성이 어떻게 바뀌는지 검증. ATR 배수/RR 등 청산 파라미터는 이미
검증된 값으로 고정(이번 라운드의 범위 밖).

데이터:
  - 바이낸스 테이커 매수/매도(지표 계산용): /api/v3/klines를 startTime으로
    페이지네이션해 과거 구간 전체를 가져온다(공개 API, 인증 불필요).
    결과는 data/vpin_backtest_cache/(gitignored, /data/ 패턴에 이미 포함)에
    원본 raw rows를 캐싱해 재실행 시 재조회하지 않는다.
  - Upbit 15분봉(체결가/ATR 계산용): data/backtest_cache/에 이미 캐싱되어
    있음(src.backtest.data_cache) — 12개 종목 전부 존재 확인됨, 추가 조회 불요.

실행:
    python scripts/vpin_backtest.py              # train/validation 스윕만
    python scripts/vpin_backtest.py --holdout "<후보 이름>"   # 확정 후보의 holdout 1회 점검
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.data_cache import load_cached_ohlcv  # noqa: E402
from src.backtest.data_split import split_time_ordered  # noqa: E402
from src.vpin_signal.binance_feed import BASE_URL, klines_to_dataframe  # noqa: E402
from src.vpin_signal.exits import VPINExitConfig, VPINPosition, check_exit  # noqa: E402
from src.vpin_signal.scanner import DEFAULT_SYMBOL_PAIRS, _atr  # noqa: E402
from src.vpin_signal.signal import VPIN_THRESHOLD_LOOKBACK, WINDOW, compute_vpin_features  # noqa: E402
from src.portfolio.tracker import PortfolioTracker  # noqa: E402

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "vpin_backtest_cache"
INTERVAL_MS = 15 * 60 * 1000

# Upbit 15m 캐시(data/backtest_cache)의 실제 커버리지에 맞춤 — 2026-02-10 ~
# 2026-09-08. "8-10개월"이 아니라 약 7개월인 이유: 2026-10-08 리서치가 썼던
# 원본 8개월 구간의 스크래치 데이터가 유실되어, 현재 로컬에 이미 캐싱되어
# 있는 Upbit 구간으로 재구성했기 때문(이 스크립트 docstring 상단 참고).
START = datetime(2026, 2, 10, tzinfo=timezone.utc)
END = datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc)

INITIAL_CASH_KRW = 1_000_000.0
POSITION_SIZE_KRW = 200_000.0
MAX_CONCURRENT_POSITIONS = 5
ATR_PERIOD = 14


# ── Historical Binance kline fetch (date-range pagination, new — not in binance_feed.py) ──

def fetch_binance_klines_range(
    symbol: str, start: datetime, end: datetime, interval: str = "15m", limit: int = 1000,
) -> pd.DataFrame:
    """[start, end] 구간 전체를 startTime 페이지네이션으로 조회.
    data/vpin_backtest_cache/{symbol}_{interval}_{start}_{end}.json에 raw
    rows를 캐싱 — 재실행 시 네트워크 호출 없이 재사용."""
    cache_file = CACHE_DIR / f"{symbol}_{interval}_{start:%Y%m%d}_{end:%Y%m%d}.json"
    if cache_file.exists():
        with open(cache_file) as f:
            rows = json.load(f)
        logger.info("Binance klines loaded from cache", symbol=symbol, bars=len(rows))
        return klines_to_dataframe(rows)

    all_rows: list[list] = []
    cursor_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    while cursor_ms <= end_ms:
        url = (
            f"{BASE_URL}?symbol={symbol}&interval={interval}"
            f"&startTime={cursor_ms}&endTime={end_ms}&limit={limit}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            batch = json.loads(resp.read().decode("utf-8"))
        if not batch:
            break
        all_rows.extend(batch)
        next_cursor = batch[-1][0] + INTERVAL_MS
        if next_cursor <= cursor_ms:
            break
        cursor_ms = next_cursor
        if len(batch) < limit:
            break
        time.sleep(0.2)  # Binance 공개 klines 레이트리밋 여유있게 지킴

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w") as f:
        json.dump(all_rows, f)
    logger.info("Binance klines fetched and cached", symbol=symbol, bars=len(all_rows))
    return klines_to_dataframe(all_rows)


# ── Per-symbol frame construction (reuses _atr verbatim) ──

def build_symbol_frame(binance_df: pd.DataFrame, upbit_df: pd.DataFrame, atr_period: int = ATR_PERIOD) -> pd.DataFrame:
    """바이낸스(volume/delta)와 Upbit(OHLC) 15분봉을 timestamp 기준 inner
    join. ATR은 scanner.py의 _atr()을 그대로 재사용(실거래와 동일 공식)."""
    merged = binance_df.merge(
        upbit_df[["timestamp", "open", "high", "low", "close"]], on="timestamp", how="inner"
    ).sort_values("timestamp").reset_index(drop=True)
    merged["atr"] = _atr(merged, atr_period)
    return merged


def build_candidate_frame(
    symbol_frame: pd.DataFrame, quantile: float, net_delta_floor_quantile: float | None,
) -> pd.DataFrame:
    """주어진 후보 설정(quantile, net_delta 하한)으로 진입조건(entry_cond)
    컬럼을 추가. VPIN/임계값/순델타 자체는 signal.compute_vpin_features()를
    그대로 호출 — 재구현하지 않음.

    net_delta 하한 후보의 정확한 식: floor = net_delta_window.rolling(200,
    min_periods=50).quantile(net_delta_floor_quantile), 단 0보다 작으면 0으로
    clip(사용자 요청 — '양(+)의 롤링 임계값'이어야 하므로 음수 floor는
    기존 >0 조건보다 느슨해지는 모순을 막기 위함). 조건은 net_delta_window
    > floor (기존의 >0을 대체, AND 아님)."""
    featured = compute_vpin_features(symbol_frame[["timestamp", "volume", "delta"]], window=WINDOW, quantile=quantile)
    df = symbol_frame.copy()
    df["vpin"] = featured["vpin"].values
    df["vpin_threshold"] = featured["vpin_threshold"].values
    df["net_delta_window"] = featured["net_delta_window"].values

    if net_delta_floor_quantile is None:
        delta_ok = df["net_delta_window"] > 0
    else:
        floor = df["net_delta_window"].rolling(VPIN_THRESHOLD_LOOKBACK, min_periods=50).quantile(
            net_delta_floor_quantile
        ).clip(lower=0)
        delta_ok = df["net_delta_window"] > floor

    cond = (df["vpin"] > df["vpin_threshold"]) & delta_ok
    df["entry_cond"] = cond.fillna(False)
    return df


# ── Simulator (mirrors VPINScanner.refresh_signal_and_scan()/check_exits() bar-by-bar) ──

@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    pnl: float
    reason: str


def _row_maps(frames: dict[str, pd.DataFrame]) -> dict[str, dict[pd.Timestamp, tuple]]:
    return {sym: {row.timestamp: row for row in df.itertuples(index=False)} for sym, df in frames.items()}


def simulate(
    frames: dict[str, pd.DataFrame],
    timestamps: list[pd.Timestamp],
    exit_cfg: VPINExitConfig,
    position_size_krw: float = POSITION_SIZE_KRW,
    max_concurrent: int = MAX_CONCURRENT_POSITIONS,
    initial_cash_krw: float = INITIAL_CASH_KRW,
) -> tuple[list[Trade], list[tuple[pd.Timestamp, float]]]:
    """VPINScanner.run_forever()의 매틱 로직(check_exits -> refresh_signal_and_scan)을
    15분봉 단위로 재현. 체결가는 해당 봉의 Upbit종가(라이브의 ticker.last 근사 —
    라이브도 15분 틱마다만 가격을 확인하므로 합리적인 근사)."""
    row_maps = _row_maps(frames)
    portfolio = PortfolioTracker(initial_cash=Decimal(str(initial_cash_krw)))
    positions: dict[str, VPINPosition] = {}
    trades: list[Trade] = []
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    position_size_dec = Decimal(str(position_size_krw))

    for ts in timestamps:
        # 1) 청산 점검 (손절 > 익절 > 타임스탑, check_exit() 그대로)
        for symbol in list(positions.keys()):
            row = row_maps[symbol].get(ts)
            if row is None:
                continue
            price = Decimal(str(row.close))
            signal = check_exit(positions[symbol], price, now=ts.to_pydatetime(), cfg=exit_cfg)
            if signal is not None:
                net_pnl = portfolio.close_position(symbol, signal.exit_price)
                pos = positions.pop(symbol)
                trades.append(Trade(symbol=symbol, entry_time=pos.entry_time, exit_time=ts,
                                     pnl=float(net_pnl), reason=signal.reason))

        # 2) 진입 스캔 (VPIN값 내림차순 랭킹, 슬롯 수만큼, 현금 가드)
        available = max_concurrent - len(positions)
        if available > 0:
            candidates = []
            for symbol, rmap in row_maps.items():
                if symbol in positions:
                    continue
                row = rmap.get(ts)
                if row is None or not row.entry_cond:
                    continue
                candidates.append((row.vpin, symbol, row))
            candidates.sort(key=lambda c: c[0], reverse=True)
            for _vpin_val, symbol, row in candidates[:available]:
                if portfolio.cash < position_size_dec:
                    break  # 라이브 현금 가드와 동일 원칙(ml_signal/scanner.py)
                if row.atr != row.atr:  # NaN
                    continue
                entry_price = Decimal(str(row.close))
                if entry_price <= 0:
                    continue
                amount = position_size_dec / entry_price
                portfolio.open_position(symbol=symbol, side="buy", amount=amount, entry_price=entry_price)
                positions[symbol] = VPINPosition.open_new(
                    symbol=symbol, entry_price=entry_price, amount=amount,
                    atr_value=float(row.atr), cfg=exit_cfg, now=ts.to_pydatetime(),
                )

        # 3) 시가평가
        prices: dict[str, Decimal] = {}
        for symbol in positions:
            row = row_maps[symbol].get(ts)
            if row is not None:
                prices[symbol] = Decimal(str(row.close))
        snap = portfolio.snapshot(prices=prices)
        fallback_prices = {s: p.entry_price for s, p in positions.items()}
        fallback_prices.update(prices)
        equity_curve.append((ts, float(snap.total_equity(fallback_prices))))

    return trades, equity_curve


@dataclass(frozen=True)
class Metrics:
    trades: int
    win_rate_pct: float
    profit_factor: float
    total_return_pct: float
    max_drawdown_pct: float

    def as_row(self, name: str) -> str:
        pf = f"{self.profit_factor:.2f}" if self.profit_factor != float("inf") else "inf"
        return (f"{name:<28} trades={self.trades:<5} win_rate={self.win_rate_pct:6.1f}%  "
                f"PF={pf:<6} return={self.total_return_pct:7.2f}%  maxDD={self.max_drawdown_pct:6.2f}%")


def compute_metrics(trades: list[Trade], equity_curve: list[tuple[pd.Timestamp, float]], initial_cash: float) -> Metrics:
    n = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = (len(wins) / n * 100) if n else 0.0
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    final_equity = equity_curve[-1][1] if equity_curve else initial_cash
    total_return_pct = (final_equity / initial_cash - 1) * 100

    peak = -float("inf")
    max_dd = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100)

    return Metrics(trades=n, win_rate_pct=win_rate, profit_factor=profit_factor,
                    total_return_pct=total_return_pct, max_drawdown_pct=max_dd)


# ── Candidate sweep ──

CANDIDATES: dict[str, dict] = {
    "baseline (q=0.90, delta>0)": dict(quantile=0.90, net_delta_floor_quantile=None),
    "q=0.93, delta>0": dict(quantile=0.93, net_delta_floor_quantile=None),
    "q=0.95, delta>0": dict(quantile=0.95, net_delta_floor_quantile=None),
    "q=0.97, delta>0": dict(quantile=0.97, net_delta_floor_quantile=None),
    "q=0.90, delta>floor(q0.5)": dict(quantile=0.90, net_delta_floor_quantile=0.5),
    "q=0.90, delta>floor(q0.7)": dict(quantile=0.90, net_delta_floor_quantile=0.7),
}


def load_all_symbol_frames() -> dict[str, pd.DataFrame]:
    frames = {}
    for upbit_sym, binance_sym in DEFAULT_SYMBOL_PAIRS:
        bdf = fetch_binance_klines_range(binance_sym, START, END)
        udf = load_cached_ohlcv(upbit_sym, "15m")
        frames[upbit_sym] = build_symbol_frame(bdf, udf)
        logger.info("Symbol frame built", symbol=upbit_sym, bars=len(frames[upbit_sym]))
    return frames


def split_master_timeline(frames: dict[str, pd.DataFrame]):
    all_ts: set[pd.Timestamp] = set()
    for df in frames.values():
        all_ts.update(df["timestamp"])
    timeline_df = pd.DataFrame({"timestamp": sorted(all_ts)})
    return split_time_ordered(timeline_df)


def run_sweep(frames: dict[str, pd.DataFrame], split) -> dict[tuple[str, str], Metrics]:
    train_ts = sorted(split.train["timestamp"])
    val_ts = sorted(split.validation["timestamp"])
    results: dict[tuple[str, str], Metrics] = {}
    for name, params in CANDIDATES.items():
        cand_frames = {sym: build_candidate_frame(frame, **params) for sym, frame in frames.items()}
        for split_name, ts_list in (("train", train_ts), ("validation", val_ts)):
            trades, equity_curve = simulate(cand_frames, ts_list, VPINExitConfig())
            results[(name, split_name)] = compute_metrics(trades, equity_curve, INITIAL_CASH_KRW)
    return results


def run_holdout(frames: dict[str, pd.DataFrame], split, candidate_name: str) -> Metrics:
    params = CANDIDATES[candidate_name]
    cand_frames = {sym: build_candidate_frame(frame, **params) for sym, frame in frames.items()}
    holdout_ts = sorted(split.holdout["timestamp"])
    trades, equity_curve = simulate(cand_frames, holdout_ts, VPINExitConfig())
    return compute_metrics(trades, equity_curve, INITIAL_CASH_KRW)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout", type=str, default=None,
                         help="train+validation에서 확정한 후보 이름으로 holdout을 '단 1회' 점검")
    args = parser.parse_args()

    frames = load_all_symbol_frames()
    split = split_master_timeline(frames)
    print(f"timeline: train={len(split.train)} bars, validation={len(split.validation)} bars, "
          f"holdout={len(split.holdout)} bars")
    print(f"train range:      {split.train['timestamp'].iloc[0]} ~ {split.train['timestamp'].iloc[-1]}")
    print(f"validation range: {split.validation['timestamp'].iloc[0]} ~ {split.validation['timestamp'].iloc[-1]}")
    print(f"holdout range:    {split.holdout['timestamp'].iloc[0]} ~ {split.holdout['timestamp'].iloc[-1]}")

    if args.holdout:
        if args.holdout not in CANDIDATES:
            raise SystemExit(f"Unknown candidate: {args.holdout}. Options: {list(CANDIDATES)}")
        metrics = run_holdout(frames, split, args.holdout)
        print("\n=== HOLDOUT (checked once) ===")
        print(metrics.as_row(args.holdout))
        return

    results = run_sweep(frames, split)
    for split_name in ("train", "validation"):
        print(f"\n=== {split_name.upper()} ===")
        for name in CANDIDATES:
            print(results[(name, split_name)].as_row(name))


if __name__ == "__main__":
    main()
