"""
MeanReversionStrategy 백테스트 스크립트 (Upbit KRW)

엔진 동작과 동일하게 시뮬레이션:
  - SL: 진입가 -1.5% ~ -3.0% (ATR 기반 클램프)
  - TP: SL 거리의 2배
  - Time-stop: 60분 보유 후 -0.5% 이하면 청산
  - Signal SELL: 최소 30분 보유 + 미실현 수익 0.3% 이상일 때만 청산
  - 수수료: 0.05% × 2 = 0.1%

실행:
    python scripts/backtest_mean_reversion.py
    python scripts/backtest_mean_reversion.py --optimize
    python scripts/backtest_mean_reversion.py --symbols BTC/KRW XRP/KRW
"""
from __future__ import annotations

import argparse
import sys
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import itertools

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from loguru import logger


COMMISSION = 0.0005   # Upbit 0.05% per side
SL_MIN_PCT = 0.015    # SL 최소 거리 1.5%
SL_MAX_PCT = 0.030    # SL 최대 거리 3.0%
TP_RR      = 2.0      # TP = SL 거리 × 2
MIN_HOLD_MIN   = 30   # 최소 보유 시간 (분)
TIME_STOP_MIN  = 60   # 타임스톱 기준 (분)
TIME_STOP_PCT  = 0.005  # 타임스톱 손실 기준 -0.5%
MIN_PROFIT_PCT = 0.003  # signal 청산 최소 수익 0.3%

DEFAULT_SYMBOLS = [
    "BTC/KRW", "ETH/KRW", "XRP/KRW", "SOL/KRW", "DOGE/KRW",
    "LINK/KRW", "ADA/KRW", "AAVE/KRW", "ONDO/KRW", "AVAX/KRW",
]


# ── 데이터 로드 ────────────────────────────────────────────────────────────────

def load_upbit_data(symbol: str, timeframe: str = "15m", limit: int = 2000) -> pd.DataFrame:
    """Upbit API는 1회 최대 200봉 제한 — 페이지네이션으로 limit봉까지 수집."""
    import time as _time
    from src.exchange.upbit import UpbitClient
    from src.config.settings import get_settings
    settings = get_settings()
    client = UpbitClient(
        access_key=settings.upbit_access_key,
        secret_key=settings.upbit_secret_key,
    )
    tf_minutes = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
    interval_ms = tf_minutes.get(timeframe, 15) * 60 * 1000
    pages = (limit + 199) // 200

    all_rows: list = []
    end_ms = int(_time.time() * 1000)
    for _ in range(pages):
        since_ms = end_ms - 200 * interval_ms
        rows = client._exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=200)
        if not rows:
            break
        all_rows = rows + all_rows
        end_ms = rows[0][0] - interval_ms
        _time.sleep(0.4)

    seen: set = set()
    unique = []
    for r in all_rows:
        if r[0] not in seen:
            seen.add(r[0])
            unique.append(r)
    unique.sort(key=lambda x: x[0])
    unique = unique[-limit:]  # 최신 limit봉만

    df = pd.DataFrame(unique, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    logger.info(f"Loaded {len(df)} bars for {symbol} ({timeframe}): {df['timestamp'].iloc[0]} ~ {df['timestamp'].iloc[-1]}")
    return df


# ── ATR 계산 ──────────────────────────────────────────────────────────────────

def _calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


# ── 단일 백테스트 ─────────────────────────────────────────────────────────────

def run_backtest(
    df: pd.DataFrame,
    symbol: str,
    strategy_params: dict,
    initial_capital: float = 100_000.0,
) -> dict:
    import src.strategy  # noqa: F401
    from src.strategy.mean_reversion import MeanReversionStrategy

    params = {**strategy_params, "symbol": symbol}
    strategy = MeanReversionStrategy(**params)
    min_bars = strategy.min_required_bars()

    capital = initial_capital
    trades = []
    in_position = False
    entry_price = entry_time = sl = tp = None

    atr_series = _calc_atr(df)

    for i in range(min_bars, len(df) - 1):
        window = df.iloc[:i + 1]
        bar = df.iloc[i]
        next_open = float(df["open"].iloc[i + 1])
        bar_time: datetime = bar["timestamp"] if hasattr(bar["timestamp"], "hour") else \
            datetime.fromtimestamp(bar["timestamp"], tz=timezone.utc)

        # ── 포지션 보호 (SL/TP/Time-stop) ────────────────────────────────────
        if in_position:
            hi = float(bar["high"])
            lo = float(bar["low"])
            hold_min = (bar_time - entry_time).total_seconds() / 60

            # TP 히트
            if hi >= tp:
                exit_p = tp
                comm = (entry_price + exit_p) * 1.0 * COMMISSION  # 단위 수량 기준
                amount = capital / entry_price
                pnl = (exit_p - entry_price) * amount - (entry_price + exit_p) * amount * COMMISSION
                capital += pnl
                trades.append({"symbol": symbol, "entry": entry_price, "exit": exit_p,
                               "pnl": pnl, "reason": "take_profit",
                               "entry_time": entry_time, "exit_time": bar_time})
                in_position = False
                continue

            # SL 히트
            if lo <= sl:
                exit_p = sl
                amount = capital / entry_price
                pnl = (exit_p - entry_price) * amount - (entry_price + exit_p) * amount * COMMISSION
                capital += pnl
                trades.append({"symbol": symbol, "entry": entry_price, "exit": exit_p,
                               "pnl": pnl, "reason": "stop_loss",
                               "entry_time": entry_time, "exit_time": bar_time})
                in_position = False
                continue

            # Time-stop
            close_p = float(bar["close"])
            if hold_min >= TIME_STOP_MIN and close_p < entry_price * (1 - TIME_STOP_PCT):
                exit_p = next_open
                amount = capital / entry_price
                pnl = (exit_p - entry_price) * amount - (entry_price + exit_p) * amount * COMMISSION
                capital += pnl
                trades.append({"symbol": symbol, "entry": entry_price, "exit": exit_p,
                               "pnl": pnl, "reason": "time_stop",
                               "entry_time": entry_time, "exit_time": bar_time})
                in_position = False
                continue

        # ── 전략 신호 ─────────────────────────────────────────────────────────
        signal = strategy.generate_signal(window)

        if signal.action.name == "BUY" and not in_position:
            entry_price = next_open * (1 + 0.0005)  # slippage
            entry_time = bar_time
            atr_val = float(atr_series.iloc[i]) if i < len(atr_series) else entry_price * 0.01
            sl_dist = max(SL_MIN_PCT, min(SL_MAX_PCT, atr_val * 2.0 / entry_price)) * entry_price
            sl = entry_price - sl_dist
            tp = entry_price + sl_dist * TP_RR
            in_position = True

        elif signal.action.name == "SELL" and in_position:
            hold_min = (bar_time - entry_time).total_seconds() / 60
            close_p = float(bar["close"])
            unrealized_pct = (close_p - entry_price) / entry_price
            if hold_min >= MIN_HOLD_MIN and unrealized_pct >= MIN_PROFIT_PCT:
                exit_p = next_open * (1 - 0.0005)
                amount = capital / entry_price
                pnl = (exit_p - entry_price) * amount - (entry_price + exit_p) * amount * COMMISSION
                capital += pnl
                trades.append({"symbol": symbol, "entry": entry_price, "exit": exit_p,
                               "pnl": pnl, "reason": "signal",
                               "entry_time": entry_time, "exit_time": bar_time})
                in_position = False

    # 마지막 포지션 강제 청산
    if in_position:
        exit_p = float(df["close"].iloc[-1])
        amount = capital / entry_price
        pnl = (exit_p - entry_price) * amount - (entry_price + exit_p) * amount * COMMISSION
        capital += pnl
        trades.append({"symbol": symbol, "entry": entry_price, "exit": exit_p,
                       "pnl": pnl, "reason": "force_close",
                       "entry_time": entry_time, "exit_time": df["timestamp"].iloc[-1]})

    total_pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    profit_factor = (
        sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses))
        if losses and sum(t["pnl"] for t in losses) != 0 else float("inf")
    )
    total_return_pct = (capital - initial_capital) / initial_capital * 100

    return {
        "symbol": symbol,
        "params": strategy_params,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "total_return_pct": total_return_pct,
        "profit_factor": profit_factor,
        "final_capital": capital,
        "trades": trades,
    }


# ── 결과 출력 ─────────────────────────────────────────────────────────────────

def print_result(r: dict) -> None:
    pf = r["profit_factor"]
    pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
    sign = "+" if r["total_pnl"] >= 0 else ""
    print(
        f"  {r['symbol']:<12} "
        f"거래:{r['total_trades']:>3}건 "
        f"({r['wins']}W/{r['losses']}L) "
        f"승률:{r['win_rate']:>5.1f}% "
        f"PnL:{sign}{r['total_pnl']:>10,.0f} "
        f"수익률:{r['total_return_pct']:>+6.2f}% "
        f"PF:{pf_str}"
    )


# ── 최적화 ────────────────────────────────────────────────────────────────────

def run_optimize(df: pd.DataFrame, symbol: str) -> list[dict]:
    param_grid = {
        "rsi_oversold_fast": [20, 25, 30],
        "rsi_oversold_slow": [30, 35, 40],
        "vol_mult":          [1.0, 1.5, 2.0],
        "rsi_exit":          [55, 60, 65],
    }
    keys = list(param_grid.keys())
    combos = list(itertools.product(*param_grid.values()))
    results = []
    for combo in combos:
        params = dict(zip(keys, combo))
        # 유효성 검사: fast < slow < exit
        if not (params["rsi_oversold_fast"] < params["rsi_oversold_slow"] < params["rsi_exit"]):
            continue
        try:
            r = run_backtest(df, symbol, params)
            if r["total_trades"] >= 5:
                results.append(r)
        except Exception:
            pass
    results.sort(key=lambda x: x["profit_factor"] if x["profit_factor"] != float("inf") else 999, reverse=True)
    return results


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="MeanReversionStrategy 백테스트 (Upbit KRW)")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--bars", type=int, default=2000, help="15분봉 수 (기본 2000 ≈ 20일)")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--optimize", action="store_true", help="파라미터 최적화 실행")
    parser.add_argument("--top-n", type=int, default=5, help="최적화 상위 N개 출력")
    args = parser.parse_args()

    # 현재 운영 파라미터
    current_params = {
        "vol_mult": 2.0,
        "rsi_oversold_fast": 20,
        "rsi_oversold_slow": 30,
        "rsi_exit": 60,
        "no_entry_hours_utc": [0, 1, 2],
    }

    print(f"\n{'='*70}")
    print(f"  MeanReversionStrategy Backtest - Upbit KRW")
    print(f"  기간: 최근 {args.bars}봉 (15분봉 ≈ {args.bars*15//60//24}일)")
    print(f"  종목: {', '.join(args.symbols)}")
    print(f"  자본: {args.capital:,.0f} KRW")
    print(f"{'='*70}\n")

    all_results = []

    for symbol in args.symbols:
        try:
            df = load_upbit_data(symbol, timeframe="15m", limit=args.bars)
        except Exception as e:
            print(f"  {symbol}: 데이터 로드 실패 — {e}")
            continue

        if args.optimize:
            print(f"\n[최적화] {symbol} — {81}개 조합 탐색 중...")
            opt_results = run_optimize(df, symbol)
            if not opt_results:
                print(f"  {symbol}: 유효한 결과 없음")
                continue
            print(f"  상위 {args.top_n}개 파라미터 조합:")
            for i, r in enumerate(opt_results[:args.top_n], 1):
                p = r["params"]
                pf = r["profit_factor"]
                pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
                print(
                    f"    {i}. RSI_fast={p['rsi_oversold_fast']} "
                    f"RSI_slow={p['rsi_oversold_slow']} "
                    f"vol={p['vol_mult']} "
                    f"exit={p['rsi_exit']} "
                    f"| {r['total_trades']}건 승률{r['win_rate']:.0f}% "
                    f"PnL{r['total_pnl']:+,.0f} PF:{pf_str}"
                )
            all_results.extend(opt_results[:3])
        else:
            r = run_backtest(df, symbol, current_params, args.capital)
            print_result(r)
            all_results.append(r)

    # ── 전체 요약 ─────────────────────────────────────────────────────────────
    if not args.optimize and all_results:
        total_pnl = sum(r["total_pnl"] for r in all_results)
        total_trades = sum(r["total_trades"] for r in all_results)
        total_wins = sum(r["wins"] for r in all_results)
        win_rate = total_wins / total_trades * 100 if total_trades else 0
        print(f"\n{'─'*70}")
        print(f"  전체 합계: {total_trades}건 | 승률 {win_rate:.1f}% | 총 PnL {total_pnl:+,.0f} KRW")

        # 종료 사유별 분석
        reason_stats: dict = {}
        for r in all_results:
            for t in r["trades"]:
                rs = t["reason"]
                if rs not in reason_stats:
                    reason_stats[rs] = {"cnt": 0, "pnl": 0.0}
                reason_stats[rs]["cnt"] += 1
                reason_stats[rs]["pnl"] += t["pnl"]
        print("\n  종료 사유별:")
        for reason, s in sorted(reason_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
            print(f"    {reason:<15}: {s['cnt']:>3}건  PnL {s['pnl']:>+10,.0f}")

    elif args.optimize and all_results:
        # 최적화 결과 전체 집계 — 가장 많이 상위권에 오른 파라미터 추천
        param_scores: dict = {}
        for r in all_results:
            key = (
                r["params"]["rsi_oversold_fast"],
                r["params"]["rsi_oversold_slow"],
                r["params"]["vol_mult"],
                r["params"]["rsi_exit"],
            )
            if key not in param_scores:
                param_scores[key] = {"total_pnl": 0, "cnt": 0, "wins": 0, "trades": 0}
            param_scores[key]["total_pnl"] += r["total_pnl"]
            param_scores[key]["cnt"] += 1
            param_scores[key]["wins"] += r["wins"]
            param_scores[key]["trades"] += r["total_trades"]

        ranked = sorted(param_scores.items(), key=lambda x: x[1]["total_pnl"], reverse=True)
        print(f"\n{'='*70}")
        print("  전체 종목 통합 최적 파라미터 Top 5:")
        for i, (key, s) in enumerate(ranked[:5], 1):
            wr = s["wins"] / s["trades"] * 100 if s["trades"] else 0
            print(
                f"    {i}. RSI_fast={key[0]} RSI_slow={key[1]} "
                f"vol={key[2]} exit={key[3]} "
                f"| 총PnL {s['total_pnl']:+,.0f} 승률{wr:.0f}%"
            )

        if ranked:
            best = ranked[0][0]
            print(f"\n  ★ 권장 파라미터:")
            print(f'    --strategy-params \"{{\\"vol_mult\\": {best[2]}, '
                  f'\\"rsi_oversold_fast\\": {best[0]}, '
                  f'\\"rsi_oversold_slow\\": {best[1]}, '
                  f'\\"rsi_exit\\": {best[3]}, '
                  f'\\"no_entry_hours_utc\\": [0, 1, 2]}}\"')


if __name__ == "__main__":
    main()
