"""Unit tests for src/backtest/data_split.py."""
from __future__ import annotations

import pandas as pd
import pytest

from src.backtest.data_split import split_time_ordered


def _make_df(n: int) -> pd.DataFrame:
    return pd.DataFrame({"value": list(range(n))})


class TestSplitTimeOrdered:
    def test_default_70_15_15_split_sizes(self) -> None:
        df = _make_df(1000)
        split = split_time_ordered(df)
        assert len(split.train) == 700
        assert len(split.validation) == 150
        assert len(split.holdout) == 150

    def test_splits_are_contiguous_and_time_ordered(self) -> None:
        df = _make_df(100)
        split = split_time_ordered(df, train_pct=0.6, validation_pct=0.2)
        assert split.train["value"].tolist() == list(range(0, 60))
        assert split.validation["value"].tolist() == list(range(60, 80))
        assert split.holdout["value"].tolist() == list(range(80, 100))

    def test_no_overlap_and_covers_whole_dataset(self) -> None:
        df = _make_df(97)  # not evenly divisible, exercises int() truncation
        split = split_time_ordered(df, train_pct=0.7, validation_pct=0.15)
        total = len(split.train) + len(split.validation) + len(split.holdout)
        assert total == 97

    def test_invalid_train_pct_raises(self) -> None:
        with pytest.raises(ValueError, match="train_pct"):
            split_time_ordered(_make_df(10), train_pct=1.5)

    def test_invalid_validation_pct_raises(self) -> None:
        with pytest.raises(ValueError, match="validation_pct"):
            split_time_ordered(_make_df(10), validation_pct=0.0)

    def test_train_plus_validation_at_or_above_one_raises(self) -> None:
        with pytest.raises(ValueError, match="train_pct \\+ validation_pct"):
            split_time_ordered(_make_df(10), train_pct=0.6, validation_pct=0.4)

    def test_holdout_never_inspected_is_a_documentation_convention(self) -> None:
        # No enforcement mechanism exists (nor should one — see
        # docs/research-protocol.md); this test only pins down that the
        # holdout split is exactly "whatever's left after train+validation",
        # so its size is predictable when researchers reason about how much
        # data they're allowed to have looked at.
        df = _make_df(1000)
        split = split_time_ordered(df, train_pct=0.8, validation_pct=0.1)
        assert len(split.holdout) == 100
