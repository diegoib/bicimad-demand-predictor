"""Smoke tests for common schemas and config."""

from src.common.schemas import StationGeometry, StationSnapshot


def test_station_snapshot_instantiation() -> None:
    snap = StationSnapshot(
        id="1",
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
