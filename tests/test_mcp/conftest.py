"""Fixtures shared across tests/test_mcp."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.common.schemas import StationInfo

_SNAP_TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture()  # type: ignore[misc]
def sample_stations() -> list[StationInfo]:
    """Five stations spread around central Madrid, for search/nearest tests."""
    return [
        StationInfo(
            station_id=1,
            name="1a - Puerta del Sol A",
            number="1a",
            activate=1,
            total_bases=24,
            dock_bikes=10,
            free_bases=14,
            latitude=40.4169,
            longitude=-3.7035,
            updated_at=_SNAP_TS,
        ),
        StationInfo(
            station_id=2,
            name="2 - Atocha",
            number="2",
            activate=1,
            total_bases=18,
            dock_bikes=0,
            free_bases=18,
            latitude=40.4066,
            longitude=-3.6906,
            updated_at=_SNAP_TS,
        ),
        StationInfo(
            station_id=3,
            name="3 - Puerta del Sol B",
            number="3",
            activate=1,
            total_bases=20,
            dock_bikes=5,
            free_bases=15,
            latitude=40.4170,
            longitude=-3.7033,
            updated_at=_SNAP_TS,
        ),
        StationInfo(
            station_id=4,
            name="4 - Chamberi",
            number="4",
            activate=1,
            total_bases=22,
            dock_bikes=22,
            free_bases=0,
            latitude=40.4348,
            longitude=-3.7025,
            updated_at=_SNAP_TS,
        ),
        StationInfo(
            station_id=5,
            name="5 - Vallecas",
            number="5",
            activate=1,
            total_bases=16,
            dock_bikes=8,
            free_bases=8,
            latitude=40.3891,
            longitude=-3.6528,
            updated_at=_SNAP_TS,
        ),
    ]
