"""Tests for src/ingestion/storage.py."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.common.schemas import BatchPredictionRow, CycleMetrics
from src.ingestion.storage import (
    _partition_key,
    load_cycle_metrics_to_bigquery,
    load_predictions_to_bigquery,
)

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
# load_predictions_to_bigquery
# ---------------------------------------------------------------------------

_SNAP_TS = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
_TARGET_TS = datetime(2026, 1, 15, 11, 0, tzinfo=UTC)

_PREDICTIONS = [
    BatchPredictionRow(
        station_id=1,
        prediction_made_at=_SNAP_TS,
        target_time=_TARGET_TS,
        predicted_dock_bikes=7.5,
        model_version="v20260115_100000",
    ),
    BatchPredictionRow(
        station_id=2,
        prediction_made_at=_SNAP_TS,
        target_time=_TARGET_TS,
        predicted_dock_bikes=3.2,
        model_version="v20260115_100000",
    ),
]

_METRICS = CycleMetrics(
    cycle_timestamp=_SNAP_TS,
    model_version="v20260115_100000",
    n_predictions=2,
    mae=2.0,
    rmse=2.5,
    p50_error=1.8,
    p90_error=3.5,
    worst_station_id=42,
    worst_station_error=4.1,
    reconciled_at=_TARGET_TS,
)


def _mock_bq_client(insert_errors: list[object] | None = None) -> MagicMock:
    mock_client = MagicMock()
    mock_client.insert_rows_json.return_value = insert_errors or []
    mock_bq = MagicMock()
    mock_bq.Client.return_value = mock_client
    return mock_bq


class TestLoadPredictionsToBigquery:
    def test_returns_row_count(self) -> None:
        mock_bq = _mock_bq_client()
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            count = load_predictions_to_bigquery(_PREDICTIONS, "proj", "dataset")
        assert count == 2

    def test_inserts_into_predictions_table(self) -> None:
        mock_bq = _mock_bq_client()
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            load_predictions_to_bigquery(_PREDICTIONS, "proj", "dataset")
        call_args = mock_bq.Client.return_value.insert_rows_json.call_args
        table_ref = call_args[0][0]
        assert table_ref.endswith("predictions")

    def test_raises_on_bq_errors(self) -> None:
        mock_bq = _mock_bq_client(insert_errors=[{"error": "bad row"}])
        with (
            patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}),
            pytest.raises(RuntimeError, match="streaming insert errors"),
        ):
            load_predictions_to_bigquery(_PREDICTIONS, "proj", "dataset")

    def test_schema_keys_match_batch_prediction_row(self) -> None:
        mock_bq = _mock_bq_client()
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            load_predictions_to_bigquery(_PREDICTIONS, "proj", "dataset")
        inserted_rows = mock_bq.Client.return_value.insert_rows_json.call_args[0][1]
        expected_keys = {
            "station_id",
            "prediction_made_at",
            "target_time",
            "predicted_dock_bikes",
            "model_version",
        }
        assert set(inserted_rows[0].keys()) == expected_keys


class TestLoadCycleMetricsToBigquery:
    def test_returns_1(self) -> None:
        mock_bq = _mock_bq_client()
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            count = load_cycle_metrics_to_bigquery(_METRICS, "proj", "dataset")
        assert count == 1

    def test_inserts_into_cycle_metrics_table(self) -> None:
        mock_bq = _mock_bq_client()
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            load_cycle_metrics_to_bigquery(_METRICS, "proj", "dataset")
        call_args = mock_bq.Client.return_value.insert_rows_json.call_args
        table_ref = call_args[0][0]
        assert table_ref.endswith("cycle_metrics")

    def test_schema_keys_match_cycle_metrics(self) -> None:
        mock_bq = _mock_bq_client()
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            load_cycle_metrics_to_bigquery(_METRICS, "proj", "dataset")
        inserted_rows = mock_bq.Client.return_value.insert_rows_json.call_args[0][1]
        expected_keys = {
            "cycle_timestamp",
            "model_version",
            "n_predictions",
            "mae",
            "rmse",
            "p50_error",
            "p90_error",
            "worst_station_id",
            "worst_station_error",
            "reconciled_at",
        }
        assert set(inserted_rows[0].keys()) == expected_keys

    def test_raises_on_bq_errors(self) -> None:
        mock_bq = _mock_bq_client(insert_errors=[{"error": "bad row"}])
        with (
            patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}),
            pytest.raises(RuntimeError, match="streaming insert errors"),
        ):
            load_cycle_metrics_to_bigquery(_METRICS, "proj", "dataset")
