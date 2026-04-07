"""Shared pytest fixtures for feature engineering tests.

Provides two synthetic Polars DataFrames:
- raw_df: 2 stations × 10 consecutive 15-min slots (2024-01-15 Mon 09:00-11:15)
- raw_df_history: same stations over 14 days (for historical feature tests)

Station 1 dock_bikes: [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
Station 2 dock_bikes: [20, 21, 22, 23, 24, 25, 26, 27, 28, 29]

Fixed weather: apparent_temperature=5.0, precipitation=1.5, direct_radiation=500.0, is_day=1
"""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from conftest import _make_raw_df


@pytest.fixture  # type: ignore[misc]
def raw_df() -> pl.DataFrame:
    """2 stations × 10 slots starting 2024-01-15 09:00 UTC (Monday)."""
    start = datetime(2024, 1, 15, 9, 0, tzinfo=UTC)
    return _make_raw_df(
        station_ids=[1, 2],
        dock_bikes_per_station=[
            [10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
            [20, 21, 22, 23, 24, 25, 26, 27, 28, 29],
        ],
        start_dt=start,
        slots=10,
    )


@pytest.fixture  # type: ignore[misc]
def raw_df_history() -> pl.DataFrame:
    """2 stations × 14 days of data (96 slots/day) for historical feature tests.

    dock_bikes is constant at 15 (station 1) and 25 (station 2) to make
    expected rolling averages trivially computable.
    """

    start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    slots_per_day = 96  # 24 * 4 (every 15 min)
    total_days = 14
    total_slots = slots_per_day * total_days

    frames = []
    for sid, constant_bikes in [(1, 15), (2, 25)]:
        rows = []
        for i in range(total_slots):
            ts = start + timedelta(minutes=15 * i)
            rows.append(
                {
                    "station_id": sid,
                    "station_number": str(sid),
                    "station_name": f"Station {sid}",
                    "snapshot_timestamp": ts,
                    "activate": 1,
                    "no_available": 0,
                    "total_bases": 24,
                    "dock_bikes": constant_bikes,
                    "free_bases": 24 - constant_bikes,
                    "latitude": 40.42 + sid * 0.01,
                    "longitude": -3.70 + sid * 0.01,
                    "temperature_2m": 10.0,
                    "apparent_temperature": 5.0,
                    "precipitation": 1.5,
                    "precipitation_probability": 80.0,
                    "wind_speed_10m": 5.0,
                    "weather_code": 61,
                    "is_day": 1,
                    "direct_radiation": 500.0,
                }
            )
        frames.append(
            pl.DataFrame(rows).with_columns(
                pl.col("snapshot_timestamp").cast(pl.Datetime("us", "UTC"))
            )
        )

    return pl.concat(frames).sort(["station_id", "snapshot_timestamp"])
