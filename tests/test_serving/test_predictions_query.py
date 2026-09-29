"""Tests for src/serving/predictions_query.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from src.serving.predictions_query import (
    load_latest_prediction_for_station,
    load_latest_predictions,
)

_SNAP_TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_TARGET_TS = _SNAP_TS + timedelta(hours=1)


def _row(station_id: int, predicted_dock_bikes: float) -> dict[str, object]:
    return {
        "station_id": station_id,
        "prediction_made_at": _SNAP_TS,
        "target_time": _TARGET_TS,
        "predicted_dock_bikes": predicted_dock_bikes,
        "model_version": "v20260101_120000",
    }


class TestLoadLatestPredictions:
    def test_returns_all_rows(self) -> None:
        with patch("google.cloud.bigquery.Client") as mock_client_cls:
            mock_client_cls.return_value.query.return_value = [
                _row(1, 5.3),
                _row(2, 8.1),
            ]
            result = load_latest_predictions()

        assert len(result) == 2
        assert {r.station_id for r in result} == {1, 2}


class TestLoadLatestPredictionForStation:
    def test_filters_query_by_station_id(self) -> None:
        with patch("google.cloud.bigquery.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.query.return_value = [_row(42, 7.0)]

            result = load_latest_prediction_for_station(42)

        assert result is not None
        assert result.station_id == 42
        assert result.predicted_dock_bikes == 7.0

        query_text, kwargs = mock_client.query.call_args
        assert "WHERE station_id = @station_id" in query_text[0]
        job_config = kwargs["job_config"]
        assert len(job_config.query_parameters) == 1
        param = job_config.query_parameters[0]
        assert param.name == "station_id"
        assert param.value == 42

    def test_returns_none_when_no_rows(self) -> None:
        with patch("google.cloud.bigquery.Client") as mock_client_cls:
            mock_client_cls.return_value.query.return_value = []
            result = load_latest_prediction_for_station(9999)

        assert result is None
