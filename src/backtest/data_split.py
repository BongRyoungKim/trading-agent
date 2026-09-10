"""
Time-ordered train/validation/holdout split for backtest research data.

See docs/research-protocol.md for the research protocol this split exists
to enforce — most importantly, the holdout split must not be inspected
(by code or by a human) until a strategy change is ready for final
validation, to keep it an honest, unbiased final check.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class DataSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    holdout: pd.DataFrame


def split_time_ordered(
    df: pd.DataFrame,
    train_pct: float = 0.70,
    validation_pct: float = 0.15,
) -> DataSplit:
    """
    Split a time-ordered OHLCV DataFrame (oldest row first) into three
    contiguous, non-overlapping chunks in chronological order:
      - train:      first `train_pct` of rows (exploration/tuning)
      - validation: next `validation_pct` of rows (walk-forward checks)
      - holdout:    remaining rows (final check only — see module docstring)

    Never shuffles — market data is sequential, and shuffling would leak
    future information into the training split.

    Raises:
        ValueError: if `train_pct`/`validation_pct` are not both in (0, 1)
            or their sum is >= 1 (holdout would then be empty or negative).
    """
    if not 0 < train_pct < 1:
        raise ValueError(f"train_pct must be in (0, 1), got {train_pct}")
    if not 0 < validation_pct < 1:
        raise ValueError(f"validation_pct must be in (0, 1), got {validation_pct}")
    if train_pct + validation_pct >= 1:
        raise ValueError(
            "train_pct + validation_pct must be < 1 so the holdout split "
            f"is non-empty, got {train_pct + validation_pct}"
        )

    n = len(df)
    train_end = int(n * train_pct)
    val_end = train_end + int(n * validation_pct)
    return DataSplit(
        train=df.iloc[:train_end].reset_index(drop=True),
        validation=df.iloc[train_end:val_end].reset_index(drop=True),
        holdout=df.iloc[val_end:].reset_index(drop=True),
    )
