"""Tests for src/ingestion/storage.py."""

from datetime import UTC, datetime

from src.ingestion.storage import _partition_key

# ---------------------------------------------------------------------------
# _partition_key tests
# ---------------------------------------------------------------------------


class TestPartitionKey:
    def test_format(self) -> None:
        ts = datetime(2025, 6, 15, 14, 30, 0, tzinfo=UTC)
        key = _partition_key(ts)
        assert key == "station_status/dt=2025-06-15/hh=14/mm=30.json"

    def test_midnight(self) -> None:
        ts = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        key = _partition_key(ts)
        assert key == "station_status/dt=2025-01-01/hh=00/mm=00.json"

    def test_zero_padded_hour_and_minute(self) -> None:
        ts = datetime(2025, 3, 5, 9, 5, 0, tzinfo=UTC)
        key = _partition_key(ts)
        assert key == "station_status/dt=2025-03-05/hh=09/mm=05.json"

    def test_ends_with_json(self) -> None:
        ts = datetime(2025, 6, 15, 14, 45, 0, tzinfo=UTC)
        assert _partition_key(ts).endswith(".json")
