"""
ml-signal 스캐너의 진입 판단(entry_threshold, 동일종목 재진입 쿨다운) 후보를
과거 데이터로 검증하는 오프라인 리서치 스크립트.

2026-10-25 기준 라이브 페이퍼 거래 9건 중 1승(승률 11%)의 원인으로 의심된
"손실 청산 직후 같은 종목 재진입" 패턴(SEI 3회, HBAR 2회)이 실제로 진입
판단의 구조적 결함인지, 아니면 표본이 작아서 생긴 노이즈/국면 효과인지를
가리기 위한 것 — docs/research-protocol.md의 train/validation/holdout 규율을
따른다.

재구현하지 않고 그대로 재사용하는 것들 (순수 로직 공유 원칙):
  - src/ml_signal/features.py  의 compute_features()
  - src/ml_signal/exits.py     의 check_exit() / MLSignalExitConfig / MLSignalPosition
  - src/ml_signal/model.py     의 학습된 단일 모델 아티팩트(재학습 안 함)
  - src/portfolio/tracker.py   의 PortfolioTracker (현금/포지션 회계)
  - src/backtest/data_cache.py 의 load_cached_ohlcv()
  - src/backtest/data_split.py 의 split_time_ordered()

실행:
    py -3.14 scripts/ml_signal_backtest.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402

from src.backtest.data_cache import load_cached_ohlcv  # noqa: E402
from src.backtest.data_split import split_time_ordered  # noqa: E402
from src.ml_signal.exits import MLSignalExitConfig, MLSignalPosition, check_exit  # noqa: E402
from src.ml_signal.features import MIN_BARS_REQUIRED, compute_features  # noqa: E402
from src.ml_signal.model import MLSignalModel, load_model  # noqa: E402
from src.ml_signal.scanner import DEFAULT_SYMBOLS  # noqa: E402

SIM_START = pd.Timestamp("2023-01-01", tz="UTC")
POSITION_SIZE_KRW = Decimal("200000")
MAX_CONCURRENT_POSITIONS = 5
INITIAL_CASH = Decimal("1000000")  # 200,000 x 5 슬롯 — 라이브 페이퍼 자본과 동일


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: Decimal
    exit_price: Decimal
    pnl: Decimal
    reason: str


@dataclass(frozen=True)
class Candidate:
    name: str
    entry_threshold: float
    cooldown_days: int  # 0 = 쿨다운 없음(현재 라이브 동작)


def load_symbol_data(symbol: str, model: MLSignalModel) -> dict:
    """심볼의 전체 캐시 이력에 대해 피처+확률을 한 번에 벡터 계산.

    model.scaler/model.model을 직접 호출하지만 수식은 MLSignalModel.
    predict_proba()와 완전히 동일(스케일링 후 predict_proba) — 한 행씩이
    아니라 전체 행렬에 대해 한 번에 계산하는 벡터화일 뿐, 로직 재구현이
    아니다.
    """
    df = load_cached_ohlcv(symbol, "1d").copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.normalize()
    featured = compute_features(df)

    valid = featured[featured[model.feature_cols].notna().all(axis=1)]
    proba_by_date: dict[pd.Timestamp, float] = {}
    if len(valid) > 0:
        X = valid[model.feature_cols].values
        X_scaled = model.scaler.transform(X)
        probas = model.model.predict_proba(X_scaled)[:, 1]
        proba_by_date = dict(zip(valid["timestamp"], probas, strict=True))

    close_by_date = dict(zip(featured["timestamp"], featured["close"], strict=True))
    return {"close": close_by_date, "proba": proba_by_date}


def build_global_dates(symbol_data: dict[str, dict], start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    all_dates: set[pd.Timestamp] = set()
    for data in symbol_data.values():
        all_dates.update(data["close"].keys())
    return sorted(d for d in all_dates if start <= d <= end)


def simulate(
    dates: list[pd.Timestamp],
    symbol_data: dict[str, dict],
    symbols: tuple[str, ...],
    candidate: Candidate,
    exit_cfg: MLSignalExitConfig,
) -> tuple[list[Trade], list[tuple[pd.Timestamp, float]]]:
    """scanner.py의 refresh_signal_and_scan()/check_exits() 알고리즘을
    일봉 단위로 그대로 재현 — 신규 포트폴리오 회계는 PortfolioTracker를
    재사용하고, 청산 판정은 check_exit()을 그대로 호출한다."""
    from src.portfolio.tracker import PortfolioTracker

    portfolio = PortfolioTracker(initial_cash=INITIAL_CASH)
    positions: dict[str, MLSignalPosition] = {}
    cooldown_until: dict[str, pd.Timestamp] = {}
    trades: list[Trade] = []
    equity_curve: list[tuple[pd.Timestamp, float]] = []

    for d in dates:
        now = d.to_pydatetime().replace(tzinfo=UTC)

        # ── 청산 체크 (라이브의 check_exits()와 동일한 우선순위: 손절 먼저) ──
        for symbol in list(positions.keys()):
            price = symbol_data[symbol]["close"].get(d)
            if price is None:
                continue
            pos = positions[symbol]
            signal = check_exit(pos, Decimal(str(price)), now=now, cfg=exit_cfg)
            if signal is None:
                continue
            net_pnl = portfolio.close_position(symbol, signal.exit_price)
            trades.append(Trade(
                symbol=symbol, entry_date=pos.entry_time, exit_date=d,
                entry_price=pos.entry_price, exit_price=signal.exit_price,
                pnl=net_pnl, reason=signal.reason,
            ))
            positions.pop(symbol)
            if candidate.cooldown_days > 0 and net_pnl < 0:
                cooldown_until[symbol] = d + pd.Timedelta(days=candidate.cooldown_days)

        # ── 진입 스캔 (라이브의 refresh_signal_and_scan()과 동일한 순서) ──
        available_slots = MAX_CONCURRENT_POSITIONS - len(positions)
        if available_slots > 0:
            ranked: list[tuple[float, str, float]] = []
            for symbol in symbols:
                if symbol in positions:
                    continue
                if candidate.cooldown_days > 0 and d < cooldown_until.get(symbol, d):
                    continue
                proba = symbol_data[symbol]["proba"].get(d)
                if proba is None or proba <= candidate.entry_threshold:
                    continue
                price = symbol_data[symbol]["close"].get(d)
                if price is None:
                    continue
                ranked.append((proba, symbol, price))
            ranked.sort(key=lambda r: r[0], reverse=True)

            for _proba, symbol, price in ranked[:available_slots]:
                if portfolio.cash < POSITION_SIZE_KRW:
                    break
                entry_price = Decimal(str(price))
                amount = POSITION_SIZE_KRW / entry_price
                portfolio.open_position(symbol=symbol, side="buy", amount=amount, entry_price=entry_price)
                positions[symbol] = MLSignalPosition.open_new(
                    symbol=symbol, entry_price=entry_price, amount=amount, cfg=exit_cfg, now=now,
                )

        # ── 일별 자산(현금 + 보유 포지션 시가평가) ──
        equity = float(portfolio.cash)
        for symbol, pos in positions.items():
            price = symbol_data[symbol]["close"].get(d, float(pos.entry_price))
            equity += float(pos.amount) * price
        equity_curve.append((d, equity))

    return trades, equity_curve


def compute_metrics(trades: list[Trade], equity_curve: list[tuple[pd.Timestamp, float]]) -> dict:
    n = len(trades)
    if n == 0:
        return {
            "n_trades": 0, "total_return_pct": 0.0, "win_rate_pct": 0.0,
            "profit_factor": float("nan"), "max_drawdown_pct": 0.0,
        }

    pnls = [float(t.pnl) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    initial_equity = equity_curve[0][1] if equity_curve else float(INITIAL_CASH)
    final_equity = equity_curve[-1][1] if equity_curve else initial_equity
    total_return_pct = (final_equity / float(INITIAL_CASH) - 1) * 100

    peak = float(INITIAL_CASH)
    max_dd = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    return {
        "n_trades": n,
        "total_return_pct": round(total_return_pct, 2),
        "win_rate_pct": round(len(wins) / n * 100, 1),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else float("inf"),
        "max_drawdown_pct": round(max_dd, 2),
    }


def run_segment(
    label: str,
    dates: list[pd.Timestamp],
    symbol_data: dict[str, dict],
    candidates: list[Candidate],
    exit_cfg: MLSignalExitConfig,
) -> dict[str, dict]:
    results = {}
    print(f"\n=== {label}  ({dates[0].date()} -> {dates[-1].date()}, {len(dates)} days) ===")
    for cand in candidates:
        trades, equity_curve = simulate(dates, symbol_data, DEFAULT_SYMBOLS, cand, exit_cfg)
        metrics = compute_metrics(trades, equity_curve)
        results[cand.name] = metrics
        print(f"{cand.name:14s} n={metrics['n_trades']:4d}  return={metrics['total_return_pct']:+7.2f}%  "
              f"WR={metrics['win_rate_pct']:5.1f}%  PF={metrics['profit_factor']:6.3f}  "
              f"MDD={metrics['max_drawdown_pct']:6.2f}%")
    return results


def main() -> int:
    model = load_model()
    print(f"Model trained_at={model.trained_at}  hold_days={model.hold_days}  "
          f"feature_cols={model.feature_cols}")

    print("Loading + scoring symbol data...")
    symbol_data = {s: load_symbol_data(s, model) for s in DEFAULT_SYMBOLS}

    sim_end = pd.Timestamp.now(tz="UTC").normalize()
    dates = build_global_dates(symbol_data, SIM_START, sim_end)
    print(f"Simulation window: {dates[0].date()} -> {dates[-1].date()}  ({len(dates)} days)")

    date_df = pd.DataFrame({"date": dates})
    split = split_time_ordered(date_df, train_pct=0.70, validation_pct=0.15)
    train_dates = split.train["date"].tolist()
    val_dates = split.validation["date"].tolist()
    holdout_dates = split.holdout["date"].tolist()
    print(f"train:      {train_dates[0].date()} -> {train_dates[-1].date()}  ({len(train_dates)} days)")
    print(f"validation: {val_dates[0].date()} -> {val_dates[-1].date()}  ({len(val_dates)} days)")
    print(f"holdout:    {holdout_dates[0].date()} -> {holdout_dates[-1].date()}  ({len(holdout_dates)} days)")

    exit_cfg = MLSignalExitConfig()  # hold_days=5, safety_stop_pct=20.0 — 라이브와 동일

    candidates = [
        Candidate("baseline", entry_threshold=0.5, cooldown_days=0),
        Candidate("cooldown_5d", entry_threshold=0.5, cooldown_days=5),
        Candidate("cooldown_10d", entry_threshold=0.5, cooldown_days=10),
        Candidate("cooldown_20d", entry_threshold=0.5, cooldown_days=20),
        Candidate("threshold_0.55", entry_threshold=0.55, cooldown_days=0),
        Candidate("threshold_0.60", entry_threshold=0.60, cooldown_days=0),
        Candidate("threshold_0.65", entry_threshold=0.65, cooldown_days=0),
    ]

    run_segment("TRAIN", train_dates, symbol_data, candidates, exit_cfg)
    run_segment("VALIDATION", val_dates, symbol_data, candidates, exit_cfg)

    # ── Decision locked in from train+validation above (see report) ──
    # Cooldown (5/10/20d) showed no consistent improving direction vs baseline
    # (mixed/worse in train, still deeply negative in validation) -> rejected.
    # Raising entry_threshold showed a consistent improving direction in BOTH
    # segments (PF/WR/MDD improve; validation flips from catastrophic loss to
    # modest profit at every threshold tried). 0.65 excluded as too thin a
    # sample (n=23/12) to trust. Of the two robust candidates, 0.55 is both
    # more profitable and has a larger sample than 0.60 -> chosen.
    # Holdout is checked exactly once, for baseline vs this locked candidate
    # only - no further search after this point.
    locked_candidate = Candidate("threshold_0.55", entry_threshold=0.55, cooldown_days=0)
    holdout_candidates = [Candidate("baseline", entry_threshold=0.5, cooldown_days=0), locked_candidate]
    run_segment("HOLDOUT (checked once)", holdout_dates, symbol_data, holdout_candidates, exit_cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
