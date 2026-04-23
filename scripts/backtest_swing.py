"""
SwingMomentumStrategy 백테스트 + 파라미터 최적화

Usage:
    python scripts/backtest_swing.py
    python scripts/backtest_swing.py --symbol BTC/KRW --days 90 --optimize
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ccxt
import pandas as pd
from loguru import logger

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtest.optimizer import StrategyOptimizer
from src.backtest.runner import BacktestConfig, BacktestRunner
from src.strategy.swing_momentum import SwingMomentumStrategy

# ── Config ────────────────────────────────────────────────────────────────────
COMMISSION = 0.0005   # Upbit maker 0.05%
SLIPPAGE   = 0.0005   # 0.05% 시장충격
POSITION_PCT = 1.0    # 거래 당 자본 100% 사용 (단일 포지션)
INITIAL_CAPITAL = 1_000_000  # 100만원

# ── Fetch historical OHLCV (paginated) ────────────────────────────────────────

def fetch_ohlcv_paginated(
    symbol: str,
    timeframe: str = "15m",
    days: int = 90,
) -> pd.DataFrame:
    """
    Upbit에서 `days`일치 15m OHLCV를 페이지네이션으로 수집.
    ccxt Upbit는 요청당 최대 200봉. 요청 사이 0.3초 딜레이(레이트리밋 대응).
    """
    exchange = ccxt.upbit({"enableRateLimit": True})
    tf_ms = _timeframe_to_ms(timeframe)
    since_ts = int((datetime.now(UTC) - timedelta(days=days)).timestamp() * 1000)

    all_rows: list[list] = []
    fetch_since = since_ts

    logger.info(
        "Fetching OHLCV",
        symbol=symbol,
        timeframe=timeframe,
        days=days,
        from_date=datetime.fromtimestamp(since_ts / 1000, UTC).strftime("%Y-%m-%d"),
    )

    while True:
        try:
            rows = exchange.fetch_ohlcv(symbol, timeframe, since=fetch_since, limit=200)
        except Exception as exc:
            logger.warning(f"API error: {exc}, retrying in 2s...")
            time.sleep(2)
            continue

        if not rows:
            break

        # Deduplicate: skip rows we already have
        new_rows = [r for r in rows if r[0] > (all_rows[-1][0] if all_rows else 0)]
        all_rows.extend(new_rows)

        last_ts = rows[-1][0]
        if last_ts >= int(datetime.now(UTC).timestamp() * 1000) - tf_ms:
            break  # 현재 봉에 도달
        if len(rows) < 200:
            break  # 마지막 페이지

        fetch_since = last_ts + 1
        time.sleep(0.3)

    df = pd.DataFrame(
        all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    df = df.astype({
        "open": float, "high": float, "low": float,
        "close": float, "volume": float,
    })

    logger.info(
        "Data fetched",
        bars=len(df),
        start=str(df["timestamp"].iloc[0])[:19],
        end=str(df["timestamp"].iloc[-1])[:19],
    )
    return df


def _timeframe_to_ms(tf: str) -> int:
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    return int(tf[:-1]) * units[tf[-1]]


# ── Backtest helpers ──────────────────────────────────────────────────────────

def run_baseline(df: pd.DataFrame, symbol: str) -> None:
    """기본 파라미터로 백테스트 실행 후 결과 출력."""
    strategy = SwingMomentumStrategy(symbol=symbol)
    config = BacktestConfig(
        initial_capital=INITIAL_CAPITAL,
        commission_pct=COMMISSION,
        slippage_pct=SLIPPAGE,
        position_size_pct=POSITION_PCT,
    )
    runner = BacktestRunner(strategy, config)
    result = runner.run(df, symbol=symbol, timeframe="15m")
    print_result("[ 기본 파라미터 ]", result)


def run_optimization(df: pd.DataFrame, symbol: str) -> dict | None:
    """그리드서치로 최적 파라미터 탐색."""
    config = BacktestConfig(
        initial_capital=INITIAL_CAPITAL,
        commission_pct=COMMISSION,
        slippage_pct=SLIPPAGE,
        position_size_pct=POSITION_PCT,
    )

    # 핵심 파라미터 탐색 — EMA 기간 포함하여 마이크로 추세 포착
    param_grid = {
        "ema_fast":          [10, 15, 20],
        "ema_slow":          [30, 40, 50],
        "rsi_oversold":      [35.0, 40.0, 45.0],
        "rsi_max":           [58.0, 65.0],
        "adx_threshold":     [18.0, 22.0, 26.0],
        "vol_mult":          [1.2, 1.5, 2.0],
        "ema_proximity_pct": [2.5, 4.0, 6.0],
    }
    total_combos = 1
    for v in param_grid.values():
        total_combos *= len(v)
    logger.info(f"Grid search: {total_combos} combinations")

    optimizer = StrategyOptimizer(
        strategy_class=SwingMomentumStrategy,
        fixed_params={"symbol": symbol},
        param_grid=param_grid,
        config=config,
        rank_by="profit_factor",
        min_trades=10,  # 최소 10거래 이상만 유효
    )

    results = optimizer.run(df)

    if not results:
        print("\n[!] 유효한 파라미터 조합 없음 (거래 횟수 부족)")
        return None

    print(f"\n[ 최적화 결과 TOP 5 ]")
    for i, r in enumerate(results[:5], 1):
        res = r.result
        ev = _calc_ev(res)
        print(
            f"  #{i} | PF={res.profit_factor:.2f} | WR={res.win_rate_pct:.1f}% "
            f"| Return={res.total_return_pct:+.2f}% | Trades={res.total_trades} "
            f"| EV={ev:+,.0f}원 | {r.params}"
        )

    best = results[0]
    print(f"\n[ 최적 파라미터 백테스트 결과 ]")
    print_result("최적 파라미터", best.result)

    return {**{"symbol": symbol}, **best.params}


def print_result(label: str, result) -> None:
    ev = _calc_ev(result)
    breakeven_wr = _calc_breakeven_wr(result)
    print(f"""
{label}
  총 수익률:      {result.total_return_pct:+.2f}%
  연환산 수익률:  {result.annualized_return_pct:+.2f}%
  총 거래 수:     {result.total_trades}
  승률:           {result.win_rate_pct:.1f}%
  Profit Factor:  {result.profit_factor:.2f}
  Sharpe Ratio:   {result.sharpe_ratio:.2f}
  최대낙폭(MDD):  {result.max_drawdown_pct:.2f}%
  기대값(EV):     {ev:+,.0f} 원/거래
  손익분기 승률:  {breakeven_wr:.1f}%
  기간:           {str(result.start_date)[:10]} ~ {str(result.end_date)[:10]}
""")


def _calc_ev(result) -> float:
    """거래당 기대값 (원)"""
    if result.total_trades == 0:
        return 0.0
    winning = [t for t in result.trades if float(t.net_pnl) > 0]
    losing  = [t for t in result.trades if float(t.net_pnl) <= 0]
    if not winning or not losing:
        return sum(float(t.net_pnl) for t in result.trades) / result.total_trades
    avg_win  = sum(float(t.net_pnl) for t in winning) / len(winning)
    avg_loss = abs(sum(float(t.net_pnl) for t in losing) / len(losing))
    wr = result.win_rate_pct / 100
    return wr * avg_win - (1 - wr) * avg_loss


def _calc_breakeven_wr(result) -> float:
    """손익분기 승률 (%)"""
    if result.total_trades == 0:
        return 0.0
    winning = [t for t in result.trades if float(t.net_pnl) > 0]
    losing  = [t for t in result.trades if float(t.net_pnl) <= 0]
    if not winning or not losing:
        return 0.0
    avg_win  = sum(float(t.net_pnl) for t in winning) / len(winning)
    avg_loss = abs(sum(float(t.net_pnl) for t in losing) / len(losing))
    return avg_loss / (avg_win + avg_loss) * 100


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SwingMomentumStrategy 백테스트")
    p.add_argument("--symbol",   default="BTC/KRW", help="거래 심볼")
    p.add_argument("--days",     type=int, default=90, help="데이터 기간(일)")
    p.add_argument("--optimize", action="store_true", help="파라미터 그리드서치 실행")
    return p


def main() -> None:
    args = build_parser().parse_args()

    df = fetch_ohlcv_paginated(args.symbol, timeframe="15m", days=args.days)

    if len(df) < 200:
        print(f"[!] 데이터 부족: {len(df)}봉. 최소 200봉 필요.")
        sys.exit(1)

    print(f"\n=== SwingMomentumStrategy 백테스트 ===")
    print(f"심볼: {args.symbol} | 기간: {args.days}일 | 봉수: {len(df)}")

    run_baseline(df, args.symbol)

    if args.optimize:
        best_params = run_optimization(df, args.symbol)
        if best_params:
            print("\n[ 전략에 반영할 파라미터 ]")
            for k, v in best_params.items():
                if k != "symbol":
                    print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
