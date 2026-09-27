"""
학습된 RandomForest 모델(joblib 아티팩트) 로딩 + 추론.

모델은 scripts/train_ml_signal_model.py로 오프라인에서 미리 학습해
data/ml_signal_model.joblib로 저장해둔다 — 라이브/페이퍼 프로세스
안에서는 학습을 절대 하지 않는다(다른 스캐너들과 같은 원칙: 무거운
연산은 별도 오프라인 과정, 실행 프로세스는 추론만).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

DEFAULT_MODEL_PATH = Path(__file__).parent.parent.parent / "data" / "ml_signal_model.joblib"


@dataclass(frozen=True)
class MLSignalModel:
    model: Any
    scaler: Any
    feature_cols: list[str]
    symbols: list[str]
    trained_at: str
    hold_days: int

    def predict_proba(self, feature_row: pd.Series) -> float:
        """feature_row는 self.feature_cols를 전부 포함해야 한다(features.py의
        latest_feature_row() 반환값 그대로). 학습 때와 동일한 순서로 정렬해
        스케일러에 넣는다 — 순서가 바뀌면 학습/추론이 어긋난다."""
        X = feature_row[self.feature_cols].values.reshape(1, -1)
        X_scaled = self.scaler.transform(X)
        return float(self.model.predict_proba(X_scaled)[0, 1])


def load_model(path: Path | str | None = None) -> MLSignalModel:
    path = Path(path) if path else DEFAULT_MODEL_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"ML signal model artifact not found at {path} — run "
            "scripts/train_ml_signal_model.py first."
        )
    data = joblib.load(path)
    return MLSignalModel(
        model=data["model"],
        scaler=data["scaler"],
        feature_cols=data["feature_cols"],
        symbols=data["symbols"],
        trained_at=data["trained_at"],
        hold_days=data["hold_days"],
    )


__all__ = ["MLSignalModel", "load_model", "DEFAULT_MODEL_PATH"]
