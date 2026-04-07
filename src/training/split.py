"""Temporal train/val/test split for BiciMAD demand forecasting.

All splits are strictly ordered in time — no shuffling, no random sampling.
The model is never exposed to future data during training or evaluation.

Usage:
    train_df, val_df, test_df = temporal_split(df, train_days=28, val_days=1, test_days=1)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import polars as pl

logger = logging.getLogger(__name__)


def temporal_split(
    df: pl.DataFrame,
    train_days: int = 28,
    val_days: int = 1,
    test_days: int = 1,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Split a featured DataFrame into train, validation, and test sets by time.

    Splits are computed from the latest timestamp backwards:
        test  = last `test_days` days
        val   = `val_days` immediately before test
        train = everything before val

    Args:
        df: Featured DataFrame with a `snapshot_timestamp` column (UTC datetime).
        train_days: Expected minimum number of training days. A warning is logged
            (not raised) if actual train data is shorter — useful when accumulating
            data early in deployment.
        val_days: Number of days reserved for validation (LightGBM early stopping).
        test_days: Number of days reserved for final held-out evaluation.

    Returns:
        Tuple of (train_df, val_df, test_df), each a Polars DataFrame.

    Raises:
        ValueError: If df is empty, or if val/test splits are empty.
    """
    if len(df) == 0:
        raise ValueError("DataFrame is empty — cannot split.")

    df = df.sort("snapshot_timestamp")

    t_max_raw = df["snapshot_timestamp"].max()
    if t_max_raw is None:
        raise ValueError("snapshot_timestamp column has no non-null values.")

    # Cast to Python datetime so arithmetic with timedelta is unambiguous for mypy
    t_max: datetime = t_max_raw  # type: ignore[assignment]
    test_cutoff = t_max - timedelta(days=test_days)
    val_cutoff = test_cutoff - timedelta(days=val_days)

    train_df = df.filter(pl.col("snapshot_timestamp") < val_cutoff)
    val_df = df.filter(
        (pl.col("snapshot_timestamp") >= val_cutoff) & (pl.col("snapshot_timestamp") < test_cutoff)
    )
    test_df = df.filter(pl.col("snapshot_timestamp") >= test_cutoff)

    if len(val_df) == 0:
        raise ValueError(
            f"Validation split is empty. Need at least {val_days + test_days + 1} days of data."
        )
    if len(test_df) == 0:
        raise ValueError(f"Test split is empty. Need at least {test_days + 1} days of data.")

    # Warn (not raise) if train is shorter than expected — early deployment scenario
    if len(train_df) > 0:
        train_min = train_df["snapshot_timestamp"].min()
        train_max = train_df["snapshot_timestamp"].max()
        actual_train_days = (train_max - train_min).days if (train_min and train_max) else 0  # type: ignore[union-attr,operator]
        if actual_train_days < train_days:
            logger.warning(
                "Train split spans only %d days (expected %d). "
                "Performance may be limited until more data accumulates.",
                actual_train_days,
                train_days,
            )
    else:
        logger.warning("Train split is empty — val and test splits consume all available data.")

    logger.info(
        "Split summary: train=%d rows [%s → %s] | val=%d rows [%s → %s] | test=%d rows [%s → %s]",
        len(train_df),
        train_df["snapshot_timestamp"].min() if len(train_df) > 0 else "—",
        train_df["snapshot_timestamp"].max() if len(train_df) > 0 else "—",
        len(val_df),
        val_df["snapshot_timestamp"].min(),
        val_df["snapshot_timestamp"].max(),
        len(test_df),
        test_df["snapshot_timestamp"].min(),
        test_df["snapshot_timestamp"].max(),
    )

    return train_df, val_df, test_df
