"""Shared pytest fixtures for training pipeline tests.

Provides:
- featured_df: 2 stations × 12 slots with non-null targets (enough for split + train).
- large_featured_df: 2 stations × 30 days for split tests requiring train/val/test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from conftest import _make_raw_df

from src.features.build_features import build_all_features


def _make_featured_df(n_slots: int, start: datetime | None = None) -> pl.DataFrame:
    """Build a small featured DataFrame for training tests."""
    start = start or datetime(2024, 1, 15, 9, 0, tzinfo=UTC)
    raw = _make_raw_df(
        station_ids=[1, 2],
        dock_bikes_per_station=[
            [10 + i for i in range(n_slots)],
            [20 + i for i in range(n_slots)],
        ],
        start_dt=start,
        slots=n_slots,
    )
    return build_all_features(raw)


@pytest.fixture  # type: ignore[misc]
def featured_df() -> pl.DataFrame:
    """2 stations × 12 slots — first 8 rows per station have non-null targets."""
    return _make_featured_df(n_slots=12)


@pytest.fixture  # type: ignore[misc]
def large_featured_df() -> pl.DataFrame:
    """2 stations × 30 days (96 slots/day) for temporal split tests.

    Uses constant dock_bikes to keep the fixture fast and deterministic.
    """
    start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    slots_per_day = 96
    total_slots = slots_per_day * 30  # 30 days

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
                    "precipitation": 0.0,
                    "precipitation_probability": 0.0,
                    "wind_speed_10m": 5.0,
                    "weather_code": 0,
                    "is_day": 1,
                    "direct_radiation": 100.0,
                }
            )
        frames.append(
            pl.DataFrame(rows).with_columns(
                pl.col("snapshot_timestamp").cast(pl.Datetime("us", "UTC"))
            )
        )

    raw = pl.concat(frames).sort(["station_id", "snapshot_timestamp"])
    return build_all_features(raw)
