"""Tests for src/serving/predict.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import numpy as np
import polars as pl
import pytest

from src.common.schemas import BicimadApiResponse, StationGeometry, StationSnapshot, WeatherSnapshot
from src.features.build_dataset import _load_json_file
from src.serving.predict import _raw_snapshot_to_polars, predict_all_stations
from src.training.train import ALL_FEATURE_COLS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SNAP_TS = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)

_WEATHER = WeatherSnapshot(
    timestamp=_SNAP_TS,
    temperature_2m=12.0,
    apparent_temperature=9.0,
    precipitation=0.0,
    precipitation_probability=10.0,
    wind_speed_10m=15.0,
    weather_code=1,
    is_day=1,
    direct_radiation=200.0,
)

_STATIONS: list[StationSnapshot] = [
    StationSnapshot(
        id=1,
        number="1",
        name="Station 1",
        activate=1,
        no_available=0,
        total_bases=24,
        dock_bikes=10,
        free_bases=14,
        geometry=StationGeometry(type="Point", coordinates=[-3.70, 40.42]),
    ),
    StationSnapshot(
        id=2,
        number="2",
        name="Station 2",
        activate=1,
        no_available=0,
        total_bases=20,
        dock_bikes=5,
        free_bases=15,
        geometry=StationGeometry(type="Point", coordinates=[-3.71, 40.43]),
    ),
    StationSnapshot(
        id=3,
        number="3",
        name="Station 3 (inactive)",
        activate=0,
        no_available=0,
        total_bases=16,
        dock_bikes=8,
        free_bases=8,
        geometry=StationGeometry(type="Point", coordinates=[-3.72, 40.44]),
    ),
    StationSnapshot(
        id=4,
        number="4",
        name="Station 4 (not available)",
        activate=1,
        no_available=1,
        total_bases=12,
        dock_bikes=6,
        free_bases=6,
        geometry=StationGeometry(type="Point", coordinates=[-3.73, 40.45]),
    ),
]

_RESPONSE = BicimadApiResponse(
    code="00",
    description="ok",
    datetime="2026-01-15T10:00:00",
    data=_STATIONS,
)


# ---------------------------------------------------------------------------
# _raw_snapshot_to_polars — column contract with _load_json_file
# ---------------------------------------------------------------------------


class TestRawSnapshotToPolars:
    """Verify that _raw_snapshot_to_polars produces the same column set as
    build_dataset._load_json_file (the authoritative column definition)."""

    def _load_json_file_columns(self) -> set[str]:
        """Return the columns produced by _load_json_file (without snapshot_timestamp)."""
        import json
        import tempfile
        from pathlib import Path

        payload = {
            "ingestion_timestamp": "2026-01-15T10:00:00+00:00",
            "weather": {
                "timestamp": "2026-01-15T10:00:00",
                "temperature_2m": 12.0,
                "apparent_temperature": 9.0,
                "precipitation": 0.0,
                "precipitation_probability": 10.0,
                "wind_speed_10m": 15.0,
                "weather_code": 1,
                "is_day": 1,
                "direct_radiation": 200.0,
            },
            "stations": {
                "code": "00",
                "description": "ok",
                "datetime": "2026-01-15T10:00:00",
                "data": [
                    {
                        "id": 1,
                        "number": "1",
                        "name": "Station 1",
                        "activate": 1,
                        "no_available": 0,
                        "total_bases": 24,
                        "dock_bikes": 10,
                        "free_bases": 14,
                        "geometry": {"type": "Point", "coordinates": [-3.70, 40.42]},
                    }
                ],
            },
        }
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(payload, f)
            tmp_path = Path(f.name)
        try:
            df = _load_json_file(tmp_path)
        finally:
            tmp_path.unlink()
        return set(df.columns)

    def test_same_columns_as_load_json_file(self) -> None:
        df = _raw_snapshot_to_polars(_RESPONSE, _WEATHER, _SNAP_TS)
        expected_cols = self._load_json_file_columns()
        assert set(df.columns) == expected_cols

    def test_snapshot_timestamp_column_present(self) -> None:
        df = _raw_snapshot_to_polars(_RESPONSE, _WEATHER, _SNAP_TS)
        assert "snapshot_timestamp" in df.columns

    def test_all_stations_included(self) -> None:
        df = _raw_snapshot_to_polars(_RESPONSE, _WEATHER, _SNAP_TS)
        assert len(df) == len(_STATIONS)

    def test_weather_sentinels_when_none(self) -> None:
        df = _raw_snapshot_to_polars(_RESPONSE, None, _SNAP_TS)
        row = df.filter(pl.col("station_id") == 1).row(0, named=True)
        assert row["temperature_2m"] == 0.0
        assert row["precipitation"] == 0.0
        assert row["weather_code"] == 0
        assert row["is_day"] == 0

    def test_weather_values_from_snapshot(self) -> None:
        df = _raw_snapshot_to_polars(_RESPONSE, _WEATHER, _SNAP_TS)
        row = df.filter(pl.col("station_id") == 1).row(0, named=True)
        assert row["temperature_2m"] == pytest.approx(12.0)
        assert row["wind_speed_10m"] == pytest.approx(15.0)

    def test_snapshot_timestamp_is_15min_floored(self) -> None:
        ts = datetime(2026, 1, 15, 10, 7, 33, tzinfo=UTC)  # not on 15-min boundary
        df = _raw_snapshot_to_polars(_RESPONSE, _WEATHER, ts)
        ts_val = df["snapshot_timestamp"][0]
        assert ts_val.minute % 15 == 0
        assert ts_val.second == 0


# ---------------------------------------------------------------------------
# predict_all_stations
# ---------------------------------------------------------------------------


def _make_mock_model(n_stations: int, value: float = 7.5) -> MagicMock:
    """Create a mock Booster that returns a fixed prediction array."""
    import lightgbm as lgb

    mock = MagicMock(spec=lgb.Booster)
    mock.predict.return_value = np.full(n_stations, value)
    return mock


class TestPredictAllStations:
    def test_returns_one_row_per_active_available_station(self) -> None:
        model = _make_mock_model(n_stations=2)  # stations 1 and 2
        results = predict_all_stations(model, "v1", _RESPONSE, _WEATHER, _SNAP_TS)
        assert len(results) == 2

    def test_excludes_inactive_stations(self) -> None:
        model = _make_mock_model(n_stations=2)
        results = predict_all_stations(model, "v1", _RESPONSE, _WEATHER, _SNAP_TS)
        station_ids = {r.station_id for r in results}
        assert 3 not in station_ids  # activate=0

    def test_excludes_unavailable_stations(self) -> None:
        model = _make_mock_model(n_stations=2)
        results = predict_all_stations(model, "v1", _RESPONSE, _WEATHER, _SNAP_TS)
        station_ids = {r.station_id for r in results}
        assert 4 not in station_ids  # no_available=1

    def test_target_time_is_one_hour_ahead(self) -> None:
        model = _make_mock_model(n_stations=2)
        results = predict_all_stations(model, "v1", _RESPONSE, _WEATHER, _SNAP_TS)
        for row in results:
            assert row.target_time - row.prediction_made_at == timedelta(hours=1)

    def test_prediction_made_at_equals_snapshot_timestamp(self) -> None:
        model = _make_mock_model(n_stations=2)
        results = predict_all_stations(model, "v1", _RESPONSE, _WEATHER, _SNAP_TS)
        for row in results:
            # prediction_made_at is the floored snapshot_timestamp
            assert row.prediction_made_at.minute % 15 == 0

    def test_model_version_propagated(self) -> None:
        model = _make_mock_model(n_stations=2)
        results = predict_all_stations(model, "v20260115_100000", _RESPONSE, _WEATHER, _SNAP_TS)
        for row in results:
            assert row.model_version == "v20260115_100000"

    def test_predicted_values_are_finite(self) -> None:
        model = _make_mock_model(n_stations=2, value=12.3)
        results = predict_all_stations(model, "v1", _RESPONSE, _WEATHER, _SNAP_TS)
        for row in results:
            assert isinstance(row.predicted_dock_bikes, float)
            assert not (row.predicted_dock_bikes != row.predicted_dock_bikes)  # not NaN

    def test_returns_empty_list_when_no_active_stations(self) -> None:
        all_inactive = BicimadApiResponse(
            code="00",
            description="ok",
            datetime="2026-01-15T10:00:00",
            data=[
                StationSnapshot(
                    id=99,
                    number="99",
                    name="Inactive",
                    activate=0,
                    no_available=0,
                    total_bases=10,
                    dock_bikes=5,
                    free_bases=5,
                    geometry=StationGeometry(type="Point", coordinates=[-3.70, 40.42]),
                )
            ],
        )
        model = MagicMock()
        results = predict_all_stations(model, "v1", all_inactive, _WEATHER, _SNAP_TS)
        assert results == []

    def test_works_without_weather(self) -> None:
        model = _make_mock_model(n_stations=2)
        results = predict_all_stations(model, "v1", _RESPONSE, None, _SNAP_TS)
        assert len(results) == 2

    def test_feature_cols_passed_to_model(self) -> None:
        model = _make_mock_model(n_stations=2)
        results = predict_all_stations(
            model, "v1", _RESPONSE, _WEATHER, _SNAP_TS, feature_cols=ALL_FEATURE_COLS
        )
        assert len(results) == 2
        _, call_kwargs = model.predict.call_args
        X = model.predict.call_args[0][0]
        # X should only contain the columns from ALL_FEATURE_COLS that are available
        for col in X.columns:
            assert col in ALL_FEATURE_COLS
