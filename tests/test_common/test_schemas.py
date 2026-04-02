"""Smoke tests for common schemas and config."""

from datetime import UTC, datetime

from src.common.schemas import StationGeometry, StationSnapshot, WeatherSnapshot


def test_station_snapshot_instantiation() -> None:
    snap = StationSnapshot(
        id=1,
        number="001",
        name="Test Station",
        activate=1,
        no_available=0,
        total_bases=24,
        dock_bikes=10,
        free_bases=14,
        geometry=StationGeometry(type="Point", coordinates=[-3.7038, 40.4168]),
    )
    assert snap.dock_bikes == 10
    assert snap.free_bases == 14


def test_weather_snapshot_instantiation() -> None:
    snap = WeatherSnapshot(
        timestamp=datetime(2025, 6, 15, 14, 0, tzinfo=UTC),
        temperature_2m=22.5,
        apparent_temperature=20.1,
        precipitation=0.0,
        precipitation_probability=10.0,
        wind_speed_10m=15.3,
        weather_code=1,
        is_day=1,
        direct_radiation=350.0,
    )
    assert snap.temperature_2m == 22.5
    assert snap.apparent_temperature == 20.1
    assert snap.is_day == 1
    assert snap.direct_radiation == 350.0
    assert snap.precipitation_probability == 10.0
