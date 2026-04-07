"""Naive baseline for BiciMAD demand forecasting.

The naive baseline predicts that dock_bikes at t+60 min equals dock_bikes at t.
This is the minimum performance bar that any trained model must beat.
"""

from __future__ import annotations

import logging
import math

import polars as pl

logger = logging.getLogger(__name__)


def naive_baseline(df: pl.DataFrame) -> dict[str, float]:
    """Compute naive baseline metrics on a featured DataFrame.

    Prediction rule: dock_bikes(t+1h) ≈ dock_bikes(t), i.e. use dock_bikes_now
    as the forecast for target_dock_bikes_1h.

    Args:
        df: Featured DataFrame with columns `dock_bikes_now` and
            `target_dock_bikes_1h`. Rows with null target are dropped.

    Returns:
        Dictionary with keys `mae` and `rmse`.

    Raises:
        ValueError: If required columns are missing or no non-null rows remain.
    """
    required = {"dock_bikes_now", "target_dock_bikes_1h"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns for naive baseline: {missing}")

    valid = df.select(["dock_bikes_now", "target_dock_bikes_1h"]).drop_nulls()
    if len(valid) == 0:
        raise ValueError("No non-null rows available to compute baseline metrics.")

    errors = valid.with_columns(
        (pl.col("dock_bikes_now").cast(pl.Float64) - pl.col("target_dock_bikes_1h")).alias("error")
    ).select(
        pl.col("error").abs().mean().alias("mae"),
        (pl.col("error").pow(2).mean()).alias("mse"),
    )

    mae = float(errors["mae"][0])
    rmse = math.sqrt(float(errors["mse"][0]))

    logger.info("Naive baseline — MAE: %.4f  RMSE: %.4f  (n=%d)", mae, rmse, len(valid))
    return {"mae": mae, "rmse": rmse}
