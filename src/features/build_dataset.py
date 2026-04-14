"""Build the training dataset from raw ingestion files.

Reads raw station snapshots from BigQuery, flattens them into a per-station
DataFrame, and applies the full feature engineering pipeline.

Usage:
    python -m src.features.build_dataset [--start-date YYYY-MM-DD]
                                         [--end-date YYYY-MM-DD]
                                         [--output PATH]
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from src.common.config import settings as _settings
from src.common.logging_setup import setup_logging
from src.features.build_features import build_all_features

logger = logging.getLogger(__name__)


def _load_json_file(path: Path) -> pl.DataFrame:
    """Load a single raw snapshot JSON and return a per-station DataFrame.

    The snapshot_timestamp is floored to the nearest 15-minute boundary to
    normalise slight timing variations between invocations.

    Args:
        path: Path to a raw snapshot JSON file.

    Returns:
        Polars DataFrame with one row per station.
    """
    with path.open() as f:
        payload = json.load(f)

    ingestion_ts = payload["ingestion_timestamp"]
    weather = payload["weather"]
    stations = payload["stations"]["data"]

    rows = []
    for s in stations:
        coords = s.get("geometry", {}).get("coordinates", [None, None])
        rows.append(
            {
                "ingestion_timestamp": ingestion_ts,
                "station_id": s["id"],
                "station_number": s["number"],
                "station_name": s["name"],
                "activate": s["activate"],
                "no_available": s["no_available"],
                "total_bases": s["total_bases"],
                "dock_bikes": s["dock_bikes"],
                "free_bases": s["free_bases"],
                "longitude": coords[0],
                "latitude": coords[1],
                # Weather fields (same for all stations in this snapshot)
                "temperature_2m": weather["temperature_2m"],
                "apparent_temperature": weather["apparent_temperature"],
                "precipitation": weather["precipitation"],
                "precipitation_probability": weather["precipitation_probability"],
                "wind_speed_10m": weather["wind_speed_10m"],
                "weather_code": weather["weather_code"],
                "is_day": weather["is_day"],
                "direct_radiation": weather["direct_radiation"],
            }
        )

    df = pl.DataFrame(rows)

    # Normalise timestamp to UTC and floor to 15-minute boundary
    # The string may contain a timezone offset (e.g. "+00:00"), use strict=False
    # to handle both tz-aware and tz-naive strings, then pin to UTC.
    df = df.with_columns(
        pl.col("ingestion_timestamp")
        .str.to_datetime(format="%Y-%m-%dT%H:%M:%S%.f%z", time_unit="us")
        .dt.convert_time_zone("UTC")
        .dt.truncate("15m")
        .alias("snapshot_timestamp")
    ).drop("ingestion_timestamp")

    return df


def _load_bigquery_snapshots(
    start_date: date | None,
    end_date: date | None,
) -> pl.DataFrame:
    """Load raw station snapshots from BigQuery.

    Args:
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.

    Returns:
        Polars DataFrame with raw station data.
    """
    try:
        from google.cloud import bigquery
    except ImportError as e:
        raise ImportError("Install google-cloud-bigquery to load snapshots from BigQuery.") from e

    client = bigquery.Client(project=_settings.gcp_project)

    where_clauses = ["s.activate = 1"]
    params = []
    if start_date:
        where_clauses.append("DATE(s.ingestion_timestamp) >= @start_date")
        params.append(bigquery.ScalarQueryParameter("start_date", "DATE", start_date.isoformat()))
    if end_date:
        where_clauses.append("DATE(s.ingestion_timestamp) <= @end_date")
        params.append(bigquery.ScalarQueryParameter("end_date", "DATE", end_date.isoformat()))

    where = " AND ".join(where_clauses)
    query = f"""
        SELECT
            s.id AS station_id,
            s.number AS station_number,
            s.name AS station_name,
            s.activate,
            s.no_available,
            s.total_bases,
            s.dock_bikes,
            s.free_bases,
            s.geometry.coordinates[ORDINAL(2)] AS latitude,
            s.geometry.coordinates[ORDINAL(1)] AS longitude,
            TIMESTAMP_TRUNC(s.ingestion_timestamp, MINUTE) AS ingestion_timestamp,
            s.weather_snapshot.temperature_2m AS temperature_2m,
            s.weather_snapshot.apparent_temperature AS apparent_temperature,
            s.weather_snapshot.precipitation AS precipitation,
            s.weather_snapshot.precipitation_probability AS precipitation_probability,
            s.weather_snapshot.wind_speed_10m AS wind_speed_10m,
            s.weather_snapshot.weather_code AS weather_code,
            s.weather_snapshot.is_day AS is_day,
            s.weather_snapshot.direct_radiation AS direct_radiation
        FROM `{_settings.gcp_project}.{_settings.bq_dataset}.station_status_raw` s
        WHERE {where}
    """

    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = client.query(query, job_config=job_config).to_dataframe()
    df = pl.from_pandas(rows)

    # Floor to 15-minute boundary
    df = df.with_columns(
        pl.col("ingestion_timestamp")
        .dt.replace_time_zone("UTC")
        .dt.truncate("15m")
        .alias("snapshot_timestamp")
    ).drop("ingestion_timestamp")

    return df


def build_training_dataset(
    start_date: date | None = None,
    end_date: date | None = None,
) -> pl.DataFrame:
    """Build a fully featured DataFrame ready for model training.

    Loads raw snapshots from BigQuery, applies build_all_features, and removes
    rows with a null training target (the last 4 rows per station).

    Args:
        start_date: Inclusive start date filter.
        end_date: Inclusive end date filter.

    Returns:
        Polars DataFrame with 35 feature columns and target_dock_bikes_1h.
        All rows have a non-null target.
    """
    # Load extra history before start_date so rolling/lag features are non-null
    # at the first training row. When start_date is None (e.g. direct API call)
    # no warmup is applied and the full table is returned.
    load_start = start_date - timedelta(days=_settings.feature_warmup_days) if start_date else None
    raw_df = _load_bigquery_snapshots(load_start, end_date)

    # Filter inactive stations before feature engineering
    raw_df = raw_df.filter(pl.col("activate") == 1)

    logger.info(
        "Raw data: %d rows, %d stations, date range [%s, %s]",
        len(raw_df),
        raw_df["station_id"].n_unique(),
        raw_df["snapshot_timestamp"].min(),
        raw_df["snapshot_timestamp"].max(),
    )

    featured_df = build_all_features(raw_df)

    # Drop rows without a target (last 4 per station — no future data)
    featured_df = featured_df.filter(pl.col("target_dock_bikes_1h").is_not_null())

    # Remove warmup rows — they were only needed for feature computation
    if start_date:
        start_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC)
        featured_df = featured_df.filter(pl.col("snapshot_timestamp") >= start_dt)

    logger.info(
        "Featured dataset: %d rows, %d columns",
        len(featured_df),
        len(featured_df.columns),
    )

    return featured_df


def build_serving_dataset() -> pl.DataFrame:
    """Build a fully featured DataFrame for batch inference.

    Same pipeline as build_training_dataset but does NOT drop rows with a null
    target — the most recent rows per station always have a null target because
    no future data exists yet, and those are exactly the rows we predict on.

    Returns:
        Polars DataFrame with all feature columns. target_dock_bikes_1h is null
        for the latest snapshot rows (the rows used for inference).
    """
    raw_df = _load_bigquery_snapshots(None, None)

    raw_df = raw_df.filter(pl.col("activate") == 1)

    logger.info(
        "Raw data for serving: %d rows, %d stations",
        len(raw_df),
        raw_df["station_id"].n_unique(),
    )

    return build_all_features(raw_df)


if __name__ == "__main__":
    import argparse

    setup_logging()

    parser = argparse.ArgumentParser(description="Build BiciMAD feature dataset")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--output", default="data/features/training_dataset.parquet")
    args = parser.parse_args()

    start = date.fromisoformat(args.start_date) if args.start_date else None
    end = date.fromisoformat(args.end_date) if args.end_date else None

    df = build_training_dataset(start_date=start, end_date=end)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(output_path)

    print(f"Saved {len(df):,} rows × {len(df.columns)} columns to {output_path}")
