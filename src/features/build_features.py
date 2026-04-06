"""Feature engineering pipeline for BiciMAD demand forecasting.

All functions accept and return a Polars DataFrame. The canonical entry point
is `build_all_features(raw_df)`, which composes all feature groups in the
correct order and appends the training target.

Leakage prevention contract:
  - Every feature for row (station_id, t) uses only data from t or earlier.
  - Lag features use .shift(n).over("station_id") with n >= 1.
  - Historical features use shift(1) before rolling/expanding aggregations.
  - The target is added AFTER all features are computed.
"""

import polars as pl

from src.features.holidays import is_holiday

# ---------------------------------------------------------------------------
# Madrid district bounding boxes — (name, lat_min, lat_max, lon_min, lon_max)
# Approximate bboxes for the 21 Madrid districts (WGS84).
# ---------------------------------------------------------------------------
_DISTRITO_BOUNDS: list[tuple[str, float, float, float, float]] = [
    ("Centro", 40.405, 40.430, -3.715, -3.690),
    ("Arganzuela", 40.385, 40.410, -3.710, -3.680),
    ("Retiro", 40.405, 40.430, -3.690, -3.660),
    ("Salamanca", 40.420, 40.455, -3.690, -3.655),
    ("Chamartin", 40.445, 40.480, -3.690, -3.650),
    ("Tetuan", 40.445, 40.480, -3.720, -3.690),
    ("Chamberi", 40.425, 40.455, -3.720, -3.690),
    ("Fuencarral-El Pardo", 40.480, 40.560, -3.760, -3.650),
    ("Moncloa-Aravaca", 40.415, 40.470, -3.760, -3.715),
    ("Latina", 40.385, 40.425, -3.760, -3.715),
    ("Carabanchel", 40.360, 40.395, -3.760, -3.700),
    ("Usera", 40.380, 40.405, -3.710, -3.680),
    ("Puente de Vallecas", 40.380, 40.415, -3.680, -3.640),
    ("Moratalaz", 40.400, 40.430, -3.660, -3.620),
    ("Ciudad Lineal", 40.430, 40.470, -3.670, -3.630),
    ("Hortaleza", 40.465, 40.510, -3.660, -3.610),
    ("Villaverde", 40.340, 40.380, -3.720, -3.660),
    ("Villa de Vallecas", 40.360, 40.400, -3.650, -3.600),
    ("Vicalvaro", 40.400, 40.440, -3.620, -3.570),
    ("San Blas-Canillejas", 40.430, 40.470, -3.630, -3.580),
    ("Barajas", 40.455, 40.500, -3.600, -3.540),
]


def _infer_distrito(lat: float, lon: float) -> str | None:
    """Return the Madrid district name for the given coordinates, or None."""
    for name, lat_min, lat_max, lon_min, lon_max in _DISTRITO_BOUNDS:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return name
    return None


# ---------------------------------------------------------------------------
# Feature builders
# ---------------------------------------------------------------------------


def build_lag_features(df: pl.DataFrame) -> pl.DataFrame:
    """Add current-state and lag features.

    Precondition: df is sorted by (station_id, snapshot_timestamp).

    Args:
        df: Raw DataFrame with dock_bikes, free_bases, total_bases columns.

    Returns:
        DataFrame with lag feature columns appended.
    """
    df = df.with_columns(
        [
            pl.col("dock_bikes").alias("dock_bikes_now"),
            pl.col("free_bases").alias("free_bases_now"),
            (pl.col("dock_bikes").cast(pl.Float64) / pl.col("total_bases").cast(pl.Float64)).alias(
                "occupancy_rate_now"
            ),
            pl.col("dock_bikes")
            .cast(pl.Float64)
            .shift(1)
            .over("station_id")
            .alias("dock_bikes_lag_15m"),
            pl.col("dock_bikes")
            .cast(pl.Float64)
            .shift(2)
            .over("station_id")
            .alias("dock_bikes_lag_30m"),
            pl.col("dock_bikes")
            .cast(pl.Float64)
            .shift(4)
            .over("station_id")
            .alias("dock_bikes_lag_1h"),
        ]
    )
    df = df.with_columns(
        (pl.col("dock_bikes_now").cast(pl.Float64) - pl.col("dock_bikes_lag_15m")).alias(
            "delta_dock_15m"
        )
    )
    return df


def build_temporal_features(df: pl.DataFrame) -> pl.DataFrame:
    """Add temporal features derived from snapshot_timestamp.

    Args:
        df: DataFrame with a snapshot_timestamp column (Datetime with timezone).

    Returns:
        DataFrame with temporal feature columns appended.
    """
    # Convert to local Madrid time for hour/minute extraction
    ts = pl.col("snapshot_timestamp").dt.convert_time_zone("Europe/Madrid")
    # dt.weekday() returns ISO weekday: 1=Mon, 7=Sun → subtract 1 for 0=Mon, 6=Sun
    dow = (ts.dt.weekday() - 1).cast(pl.Int32)
    # Cast to Int32 before multiplication to prevent Int8 overflow (hour=23 * 60 = 1380 > 127)
    msm = ts.dt.hour().cast(pl.Int32) * 60 + ts.dt.minute().cast(pl.Int32)

    df = df.with_columns(
        [
            ts.dt.hour().alias("hour_of_day"),
            dow.alias("day_of_week"),
            (dow >= 5).alias("is_weekend"),
            ts.dt.month().alias("month"),
            msm.alias("minutes_since_midnight"),
        ]
    )

    # is_holiday: map per-row from the date value
    df = df.with_columns(
        pl.col("snapshot_timestamp")
        .dt.convert_time_zone("Europe/Madrid")
        .dt.date()
        .map_elements(is_holiday, return_dtype=pl.Boolean)
        .alias("is_holiday")
    )

    # is_rush_hour: weekday 07:00-09:30 or 17:00-20:00
    df = df.with_columns(
        (
            (~pl.col("is_weekend"))
            & (
                pl.col("minutes_since_midnight").is_between(420, 570)
                | pl.col("minutes_since_midnight").is_between(1020, 1199)
            )
        ).alias("is_rush_hour")
    )

    return df


def build_weather_features(df: pl.DataFrame) -> pl.DataFrame:
    """Add weather features (rename, cast, and derive boolean flags).

    Args:
        df: DataFrame with raw weather columns from Open-Meteo.

    Returns:
        DataFrame with weather feature columns appended.
    """
    df = df.with_columns(
        [
            pl.col("precipitation").alias("precipitation_mm"),
            pl.col("is_day").cast(pl.Boolean),
            (pl.col("precipitation") > 0.0).alias("is_raining"),
            (pl.col("apparent_temperature") < 8.0).alias("feels_cold"),
            (pl.col("apparent_temperature") > 30.0).alias("feels_hot"),
            (pl.col("direct_radiation") > 400.0).alias("high_solar_radiation"),
        ]
    )
    return df


def build_historical_features(df: pl.DataFrame) -> pl.DataFrame:
    """Add historical statistical features.

    Requires hour_of_day and day_of_week columns (from build_temporal_features).
    Precondition: df is sorted by (station_id, snapshot_timestamp).

    Features are computed without data leakage: each value for day D uses
    only data from day D-1 and earlier (via shift(1) before rolling/expanding).

    Args:
        df: DataFrame with dock_bikes, hour_of_day, day_of_week, station_id,
            and snapshot_timestamp columns.

    Returns:
        DataFrame with historical feature columns appended.
    """
    df = df.with_columns(pl.col("snapshot_timestamp").dt.date().alias("_date"))

    # ------------------------------------------------------------------
    # avg_dock_same_hour_7d and std_dock_same_hour_7d
    # ------------------------------------------------------------------
    daily_hourly = (
        df.group_by(["station_id", "_date", "hour_of_day"])
        .agg(pl.col("dock_bikes").mean().alias("_daily_mean"))
        .sort(["station_id", "hour_of_day", "_date"])
        .with_columns(
            [
                pl.col("_daily_mean")
                .shift(1)
                .rolling_mean(window_size=7, min_samples=1)
                .over(["station_id", "hour_of_day"])
                .alias("avg_dock_same_hour_7d"),
                pl.col("_daily_mean")
                .shift(1)
                .rolling_std(window_size=7, min_samples=2)
                .over(["station_id", "hour_of_day"])
                .alias("std_dock_same_hour_7d"),
            ]
        )
    )

    df = df.join(
        daily_hourly.select(
            ["station_id", "_date", "hour_of_day", "avg_dock_same_hour_7d", "std_dock_same_hour_7d"]
        ),
        on=["station_id", "_date", "hour_of_day"],
        how="left",
    )

    # ------------------------------------------------------------------
    # avg_dock_same_weekday — expanding mean for same (station, weekday, hour)
    # ------------------------------------------------------------------
    daily_weekday = (
        df.group_by(["station_id", "_date", "day_of_week", "hour_of_day"])
        .agg(pl.col("dock_bikes").mean().alias("_dw_mean"))
        .sort(["station_id", "day_of_week", "hour_of_day", "_date"])
        .with_columns(
            pl.col("_dw_mean")
            .shift(1)
            .rolling_mean(window_size=10_000, min_samples=1)
            .over(["station_id", "day_of_week", "hour_of_day"])
            .alias("avg_dock_same_weekday")
        )
    )

    df = df.join(
        daily_weekday.select(
            ["station_id", "_date", "day_of_week", "hour_of_day", "avg_dock_same_weekday"]
        ),
        on=["station_id", "_date", "day_of_week", "hour_of_day"],
        how="left",
    )

    # ------------------------------------------------------------------
    # station_daily_turnover — 7-day rolling mean of daily |dock_bikes diff|
    # ------------------------------------------------------------------
    df_with_delta = df.with_columns(
        pl.col("dock_bikes").diff().abs().over("station_id").alias("_dock_delta_abs")
    )

    daily_turnover = (
        df_with_delta.group_by(["station_id", "_date"])
        .agg(pl.col("_dock_delta_abs").sum().alias("_daily_turnover"))
        .sort(["station_id", "_date"])
        .with_columns(
            pl.col("_daily_turnover")
            .shift(1)
            .rolling_mean(window_size=7, min_samples=1)
            .over("station_id")
            .alias("station_daily_turnover")
        )
    )

    df = df.join(
        daily_turnover.select(["station_id", "_date", "station_daily_turnover"]),
        on=["station_id", "_date"],
        how="left",
    )

    # ------------------------------------------------------------------
    # dock_bikes_same_time_1w — self-join shifted back 7 days
    # Each row in `lookup` represents "dock_bikes at T", keyed by "T + 7 days".
    # Joining on (station_id, snapshot_timestamp = T + 7d) finds the value from T.
    # ------------------------------------------------------------------
    lookup = df.select(
        [
            pl.col("station_id"),
            (pl.col("snapshot_timestamp") + pl.duration(days=7)).alias("snapshot_timestamp"),
            pl.col("dock_bikes").cast(pl.Float64).alias("dock_bikes_same_time_1w"),
        ]
    )

    df = df.join(lookup, on=["station_id", "snapshot_timestamp"], how="left")

    # Drop helper column
    df = df.drop("_date")

    return df


def build_station_features(df: pl.DataFrame) -> pl.DataFrame:
    """Add static station features.

    station_id, total_bases, latitude, longitude are already present in the
    raw DataFrame. This function adds the distrito categorical feature.

    Args:
        df: DataFrame with latitude and longitude columns.

    Returns:
        DataFrame with distrito column appended.
    """
    df = df.with_columns(
        pl.struct(["latitude", "longitude"])
        .map_elements(
            lambda s: _infer_distrito(s["latitude"], s["longitude"]),
            return_dtype=pl.String,
        )
        .alias("distrito")
    )
    return df


# ---------------------------------------------------------------------------
# Main composition function
# ---------------------------------------------------------------------------


def build_all_features(raw_df: pl.DataFrame) -> pl.DataFrame:
    """Transform a raw station snapshot DataFrame into a fully featured DataFrame.

    This is the single entry point for feature engineering used by both the
    training pipeline and the serving layer. Calling this function with the
    same input always produces the same output (no side effects).

    The target column (target_dock_bikes_1h) is added last to prevent any
    possibility of leakage into features. During inference, it will be null
    for all rows.

    Args:
        raw_df: DataFrame with one row per (station_id, snapshot_timestamp),
            including raw station fields and weather fields.

    Returns:
        DataFrame with all 35 feature columns plus target_dock_bikes_1h.
        Rows are sorted by (station_id, snapshot_timestamp).
    """
    df = raw_df.clone()

    # 1. Canonical sort required by shift-based lag and historical functions
    df = df.sort(["station_id", "snapshot_timestamp"])

    # 2. Deduplicate: keep last row if two map to the same (station, timestamp)
    df = df.unique(subset=["station_id", "snapshot_timestamp"], keep="last", maintain_order=True)

    # 3. Build features in dependency order
    df = build_lag_features(df)
    df = build_temporal_features(df)  # produces hour_of_day, day_of_week
    df = build_weather_features(df)
    df = build_historical_features(df)  # depends on hour_of_day, day_of_week
    df = build_station_features(df)

    # 4. Target: dock_bikes 60 min into the future (4 × 15-min slots)
    #    shift(-4) per station → null for last 4 rows of each station
    df = df.with_columns(
        pl.col("dock_bikes")
        .cast(pl.Float64)
        .shift(-4)
        .over("station_id")
        .alias("target_dock_bikes_1h")
    )

    return df
