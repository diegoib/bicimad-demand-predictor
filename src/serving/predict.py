"""Batch inference for BiciMAD demand forecasting.

Given the live API response and current weather, builds features for the
current snapshot and runs the LightGBM model to produce t+1h predictions
for all active stations.

This module is called from src/ingestion/main.py after each successful
ingestion cycle.  Using the same feature-engineering code as training
eliminates training-serving skew.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import lightgbm as lgb
import pandas as pd
import polars as pl

from src.common.schemas import BatchPredictionRow, BicimadApiResponse, WeatherSnapshot
from src.features.build_features import build_all_features
from src.training.train import _BOOL_FEATURE_NAMES, ALL_FEATURE_COLS, CATEGORICAL_FEATURES

logger = logging.getLogger(__name__)

# Sentinel values used when weather data is unavailable
_WEATHER_SENTINELS: dict[str, Any] = {
    "temperature_2m": 0.0,
    "apparent_temperature": 0.0,
    "precipitation": 0.0,
    "precipitation_probability": 0.0,
    "wind_speed_10m": 0.0,
    "weather_code": 0,
    "is_day": 0,
    "direct_radiation": 0.0,
}


def _raw_snapshot_to_polars(
    stations_response: BicimadApiResponse,
    weather: WeatherSnapshot | None,
    snapshot_timestamp: datetime,
) -> pl.DataFrame:
    """Convert a live API response into the same shape as _load_json_file.

    Produces exactly the same columns as build_dataset._load_json_file so that
    build_all_features() can be applied identically to training data.

    Args:
        stations_response: Parsed BiciMAD API response.
        weather: Current weather snapshot, or None if unavailable.
        snapshot_timestamp: The UTC timestamp of this ingestion cycle
            (already floored to 15-min boundary in main.py).

    Returns:
        Polars DataFrame with one row per active station.
    """
    weather_vals: dict[str, Any]
    if weather is not None:
        weather_vals = {
            "temperature_2m": weather.temperature_2m,
            "apparent_temperature": weather.apparent_temperature,
            "precipitation": weather.precipitation,
            "precipitation_probability": weather.precipitation_probability,
            "wind_speed_10m": weather.wind_speed_10m,
            "weather_code": weather.weather_code,
            "is_day": weather.is_day,
            "direct_radiation": weather.direct_radiation,
        }
    else:
        weather_vals = _WEATHER_SENTINELS.copy()

    rows = []
    for s in stations_response.data:
        coords = s.geometry.coordinates  # [longitude, latitude]
        longitude = coords[0] if len(coords) > 0 else None
        latitude = coords[1] if len(coords) > 1 else None
        rows.append(
            {
                "station_id": s.id,
                "station_number": s.number,
                "station_name": s.name,
                "activate": s.activate,
                "no_available": s.no_available,
                "total_bases": s.total_bases,
                "dock_bikes": s.dock_bikes,
                "free_bases": s.free_bases,
                "longitude": longitude,
                "latitude": latitude,
                **weather_vals,
            }
        )

    df = pl.DataFrame(rows)

    # Pin snapshot_timestamp (already 15-min floored by caller)
    ts_floored = snapshot_timestamp.replace(second=0, microsecond=0)
    # Floor to 15-min boundary
    minutes = (ts_floored.minute // 15) * 15
    ts_floored = ts_floored.replace(minute=minutes)

    df = df.with_columns(
        pl.lit(ts_floored).cast(pl.Datetime("us", "UTC")).alias("snapshot_timestamp")
    )

    return df


def _prepare_serving_features(
    df: pl.DataFrame,
    feature_cols: list[str] | None = None,
) -> pd.DataFrame:
    """Convert a featured Polars DataFrame to a pandas X matrix for inference.

    Mirrors _prepare_features from train.py but does NOT drop null-target rows —
    during serving, target_dock_bikes_1h is always null.

    Args:
        df: Featured Polars DataFrame (output of build_all_features).
        feature_cols: Feature columns to use. Defaults to ALL_FEATURE_COLS.

    Returns:
        pandas DataFrame ready for lgb.Booster.predict().
    """
    cols = feature_cols if feature_cols is not None else ALL_FEATURE_COLS

    # Cast booleans to Int8 (LightGBM rejects Python bool)
    bool_present = [c for c in _BOOL_FEATURE_NAMES if c in df.columns and c in cols]
    if bool_present:
        df = df.with_columns([pl.col(c).cast(pl.Int8) for c in bool_present])

    available_cols = [c for c in cols if c in df.columns]
    X = df.select(available_cols).to_pandas()

    for cat_col in CATEGORICAL_FEATURES:
        if cat_col in X.columns:
            X[cat_col] = X[cat_col].astype("category")

    return X


def predict_all_stations(
    model: lgb.Booster,
    model_version: str,
    stations_response: BicimadApiResponse,
    weather: WeatherSnapshot | None,
    snapshot_timestamp: datetime,
    feature_cols: list[str] | None = None,
    historical_df: pl.DataFrame | None = None,
) -> list[BatchPredictionRow]:
    """Run batch inference for all active stations in the current snapshot.

    Uses the same feature-engineering code as training (build_all_features)
    to guarantee zero training-serving skew.  When ``historical_df`` is
    provided, it is prepended to the current snapshot before feature
    engineering so that lag and historical rolling features are populated
    correctly.  Without it, those columns will be null (LightGBM handles
    missing values, but predictions will be less accurate).

    Args:
        model: Trained lgb.Booster loaded from GCS.
        model_version: Version string stored in metadata.json (e.g. "v20260101_120000").
        stations_response: Parsed BiciMAD API response from the current cycle.
        weather: Current weather snapshot, or None if Open-Meteo was unavailable.
        snapshot_timestamp: UTC timestamp of the ingestion cycle.
        feature_cols: Override feature columns (defaults to ALL_FEATURE_COLS).
        historical_df: Optional DataFrame of recent raw snapshots in the same
            column format as ``_raw_snapshot_to_polars`` output (i.e. from
            ``_load_bigquery_snapshots``).  Typically the last 7-8 days.
            When supplied, lag and historical features are computed correctly.

    Returns:
        List of BatchPredictionRow, one per active and available station.
        Empty if no active stations are found.
    """
    current_df = _raw_snapshot_to_polars(stations_response, weather, snapshot_timestamp)

    # Determine the floored current timestamp (set by _raw_snapshot_to_polars)
    current_ts = current_df["snapshot_timestamp"][0]

    # Only predict on active, available stations (from the current snapshot)
    active_ids = (
        current_df.filter((pl.col("activate") == 1) & (pl.col("no_available") == 0))["station_id"]
        .unique()
        .to_list()
    )

    if not active_ids:
        logger.warning("No active stations in snapshot — skipping prediction")
        return []

    # Build the combined DataFrame for feature engineering.
    # historical_df (if provided) supplies the past context needed for lags
    # and 7-day rolling statistics.  We filter it to the same active station IDs
    # to keep the computation focused, and drop any rows that coincide with the
    # current timestamp (avoid duplicates).
    if historical_df is not None and not historical_df.is_empty():
        hist = historical_df.filter(
            pl.col("station_id").is_in(active_ids) & (pl.col("snapshot_timestamp") < current_ts)
        )
        active_current = current_df.filter(pl.col("station_id").is_in(active_ids))
        combined_df = pl.concat([hist, active_current], how="diagonal")
        logger.debug(
            "Feature engineering on %d historical + %d current rows",
            len(hist),
            len(active_current),
        )
    else:
        combined_df = current_df.filter(pl.col("station_id").is_in(active_ids))
        logger.debug("No historical context — lag features will be null")

    featured_df = build_all_features(combined_df)

    # Keep only the rows for the current snapshot timestamp
    current_rows = featured_df.filter(pl.col("snapshot_timestamp") == current_ts)

    if current_rows.is_empty():
        logger.warning("build_all_features returned no rows for current snapshot timestamp")
        return []

    X = _prepare_serving_features(current_rows, feature_cols)

    raw_preds = model.predict(X)

    target_time = snapshot_timestamp + timedelta(hours=1)
    prediction_made_at = snapshot_timestamp

    station_ids = current_rows["station_id"].to_list()
    results: list[BatchPredictionRow] = []
    for station_id, pred_value in zip(station_ids, raw_preds):
        results.append(
            BatchPredictionRow(
                station_id=int(station_id),
                prediction_made_at=prediction_made_at,
                target_time=target_time,
                predicted_dock_bikes=float(pred_value),
                model_version=model_version,
            )
        )

    logger.info(
        "Predicted %d stations for target_time=%s (model %s)",
        len(results),
        target_time.isoformat(),
        model_version,
    )
    return results
