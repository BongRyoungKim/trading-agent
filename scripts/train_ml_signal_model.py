"""
다변량 ML 신호(RandomForest) 모델 오프라인 학습 스크립트.

    python scripts/train_ml_signal_model.py

src/ml_signal/features.py(단일 소스)로 피처를 계산하고, 2026-09-27
리서치에서 검증된 설정(RandomForestClassifier n_estimators=100,
max_depth=3, min_samples_leaf=100 — 피처 확장·손절 추가·하이퍼파라미터
튜닝은 전부 워크포워드 최종 구간에서 역효과였음, 가장 단순한 최초 설정이
최선이었음)으로 전체 캐시 이력에 대해 학습 후 data/ml_signal_model.joblib
로 저장한다.

라이브/페이퍼 프로세스(src/ml_signal/scanner.py, runner.py)는 이 파일을
읽기만 하고 절대 재학습하지 않는다 — 재학습이 필요하면 이 스크립트를
다시 실행하고 서버에 새 아티팩트를 배포한다(수동 과정, 자동화 없음).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import joblib  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.ensemble import RandomForestClassifier  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

from src.backtest.data_cache import load_cached_ohlcv  # noqa: E402
from src.ml_signal.features import FEATURE_COLS, HOLD_DAYS, compute_features  # noqa: E402
from src.ml_signal.model import DEFAULT_MODEL_PATH  # noqa: E402
from src.ml_signal.scanner import DEFAULT_SYMBOLS  # noqa: E402

LABEL_THRESHOLD_PCT = 1.5
RF_PARAMS = dict(n_estimators=100, max_depth=3, min_samples_leaf=100, class_weight="balanced", random_state=42)


def _build_labeled_symbol(symbol: str) -> pd.DataFrame:
    df = load_cached_ohlcv(symbol, "1d").copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.normalize()
    df = compute_features(df)
    df["fwd_ret_pct"] = (df["close"].shift(-HOLD_DAYS) / df["close"] - 1) * 100
    df["label"] = (df["fwd_ret_pct"] > LABEL_THRESHOLD_PCT).astype(int)
    df["symbol"] = symbol
    return df


def build_training_dataset() -> pd.DataFrame:
    frames = [_build_labeled_symbol(s) for s in DEFAULT_SYMBOLS]
    pooled = pd.concat(frames, ignore_index=True)
    return pooled.dropna(subset=FEATURE_COLS + ["label", "fwd_ret_pct"]).reset_index(drop=True)


def main() -> int:
    print(f"Building training dataset from {len(DEFAULT_SYMBOLS)} symbols...")
    pooled = build_training_dataset()
    print(f"n_rows={len(pooled)}  label_rate={pooled['label'].mean():.3f}  "
          f"date_range={pooled.timestamp.min().date()} -> {pooled.timestamp.max().date()}")

    scaler = StandardScaler().fit(pooled[FEATURE_COLS].values)
    model = RandomForestClassifier(**RF_PARAMS).fit(
        scaler.transform(pooled[FEATURE_COLS].values), pooled["label"].values,
    )

    DEFAULT_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "model": model,
        "scaler": scaler,
        "feature_cols": FEATURE_COLS,
        "symbols": list(DEFAULT_SYMBOLS),
        "trained_at": str(pooled.timestamp.max().date()),
        "hold_days": HOLD_DAYS,
    }, DEFAULT_MODEL_PATH)
    print(f"Saved {DEFAULT_MODEL_PATH} ({DEFAULT_MODEL_PATH.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
