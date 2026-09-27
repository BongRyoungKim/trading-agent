"""Unit tests for src/ml_signal/model.py (모델 아티팩트 로딩 + 추론)."""
from __future__ import annotations

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from src.ml_signal.model import load_model


FEATURE_COLS = ["f1", "f2", "f3"]


@pytest.fixture
def fake_model_path(tmp_path):
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, len(FEATURE_COLS)))
    y = (X[:, 0] + X[:, 1] > 0).astype(int)
    scaler = StandardScaler().fit(X)
    model = LogisticRegression().fit(scaler.transform(X), y)

    path = tmp_path / "fake_model.joblib"
    joblib.dump({
        "model": model, "scaler": scaler, "feature_cols": FEATURE_COLS,
        "symbols": ["BTC/KRW", "ETH/KRW"], "trained_at": "2026-09-27", "hold_days": 5,
    }, path)
    return path


class TestLoadModel:
    def test_raises_when_file_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_model(tmp_path / "does_not_exist.joblib")

    def test_loads_all_fields(self, fake_model_path):
        m = load_model(fake_model_path)
        assert m.feature_cols == FEATURE_COLS
        assert m.symbols == ["BTC/KRW", "ETH/KRW"]
        assert m.trained_at == "2026-09-27"
        assert m.hold_days == 5


class TestPredictProba:
    def test_returns_probability_between_0_and_1(self, fake_model_path):
        m = load_model(fake_model_path)
        row = pd.Series({"f1": 0.5, "f2": 0.5, "f3": -0.2})
        proba = m.predict_proba(row)
        assert 0.0 <= proba <= 1.0

    def test_higher_signal_features_give_higher_probability(self, fake_model_path):
        m = load_model(fake_model_path)
        low = m.predict_proba(pd.Series({"f1": -3.0, "f2": -3.0, "f3": 0.0}))
        high = m.predict_proba(pd.Series({"f1": 3.0, "f2": 3.0, "f3": 0.0}))
        assert high > low

    def test_extra_columns_in_feature_row_are_ignored(self, fake_model_path):
        # feature_row가 feature_cols 외에 다른 컬럼(예: symbol, timestamp)을
        # 더 갖고 있어도(scanner에서 실제로 그럴 것) 문제없이 동작해야 함
        m = load_model(fake_model_path)
        row = pd.Series({"f1": 0.5, "f2": 0.5, "f3": -0.2, "symbol": "BTC/KRW", "timestamp": "2026-09-27"})
        proba = m.predict_proba(row)
        assert 0.0 <= proba <= 1.0
