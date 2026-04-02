"""Tests for src/ingestion/storage.py."""

import json
from datetime import UTC, datetime
from pathlib import Path

from src.ingestion.storage import _partition_key, write_raw_to_local

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


# ---------------------------------------------------------------------------
# write_raw_to_local tests
# ---------------------------------------------------------------------------


class TestWriteRawToLocal:
    def test_creates_file_at_correct_path(self, tmp_path: Path) -> None:
        ts = datetime(2025, 6, 15, 14, 30, 0, tzinfo=UTC)
        payload = {"stations": [], "weather": {}, "ingestion_timestamp": ts.isoformat()}

        output = write_raw_to_local(payload, str(tmp_path), ts)

        expected = tmp_path / "station_status" / "dt=2025-06-15" / "hh=14" / "mm=30.json"
        assert output == expected
        assert expected.exists()

    def test_written_file_is_valid_json(self, tmp_path: Path) -> None:
        ts = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        payload = {"key": "value", "number": 42}

        output = write_raw_to_local(payload, str(tmp_path), ts)

        loaded = json.loads(output.read_text(encoding="utf-8"))
        assert loaded["key"] == "value"
        assert loaded["number"] == 42

    def test_creates_nested_parent_directories(self, tmp_path: Path) -> None:
        ts = datetime(2025, 12, 31, 23, 45, 0, tzinfo=UTC)
        write_raw_to_local({}, str(tmp_path), ts)

        partition_dir = tmp_path / "station_status" / "dt=2025-12-31" / "hh=23"
        assert partition_dir.is_dir()

    def test_returns_path_object(self, tmp_path: Path) -> None:
        ts = datetime(2025, 6, 15, 8, 15, 0, tzinfo=UTC)
        result = write_raw_to_local({}, str(tmp_path), ts)
        assert isinstance(result, Path)

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        ts = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        write_raw_to_local({"v": 1}, str(tmp_path), ts)
        write_raw_to_local({"v": 2}, str(tmp_path), ts)

        output = tmp_path / "station_status" / "dt=2025-06-15" / "hh=10" / "mm=00.json"
        loaded = json.loads(output.read_text())
        assert loaded["v"] == 2
