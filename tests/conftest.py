"""Top-level pytest fixtures shared across all test modules."""

from datetime import UTC, datetime, timedelta

import polars as pl


def _make_raw_df(
    station_ids: list[int],
    dock_bikes_per_station: list[list[int]],
    start_dt: datetime,
    slots: int,
    apparent_temperature: float = 5.0,
    precipitation: float = 1.5,
    direct_radiation: float = 500.0,
) -> pl.DataFrame:
    """Build a synthetic raw snapshot DataFrame."""
    rows = []
    for sid, bikes_seq in zip(station_ids, dock_bikes_per_station):
        for i in range(slots):
            ts = datetime(
                start_dt.year,
                start_dt.month,
                start_dt.day,
                start_dt.hour,
                start_dt.minute,
                tzinfo=UTC,
            )
            ts = ts + timedelta(minutes=15 * i)

            rows.append(
                {
                    "station_id": sid,
                    "station_number": str(sid),
                    "station_name": f"Station {sid}",
                    "snapshot_timestamp": ts,
                    "activate": 1,
                    "no_available": 0,
                    "total_bases": 24,
                    "dock_bikes": bikes_seq[i],
                    "free_bases": 24 - bikes_seq[i],
                    "latitude": 40.42 + sid * 0.01,
                    "longitude": -3.70 + sid * 0.01,
                    # Weather
                    "temperature_2m": 10.0,
                    "apparent_temperature": apparent_temperature,
                    "precipitation": precipitation,
                    "precipitation_probability": 80.0,
                    "wind_speed_10m": 5.0,
                    "weather_code": 61,
                    "is_day": 1,
                    "direct_radiation": direct_radiation,
                }
            )

    return pl.DataFrame(rows).with_columns(
        pl.col("snapshot_timestamp").cast(pl.Datetime("us", "UTC"))
    )
