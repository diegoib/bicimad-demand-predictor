"""Tests for src/monitoring/reconcile.py."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.common.schemas import BicimadApiResponse, CycleMetrics, StationGeometry, StationSnapshot
from src.monitoring.reconcile import reconcile_predictions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SNAP_TS = datetime(2026, 1, 15, 11, 0, tzinfo=UTC)
_PRED_TS = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
_PROJECT = "test-project"
_DATASET = "bicimad"


def _make_response(stations: list[tuple[int, int]]) -> BicimadApiResponse:
    """Build a BicimadApiResponse from a list of (station_id, dock_bikes) tuples."""
    data = [
        StationSnapshot(
            id=sid,
            number=str(sid),
            name=f"Station {sid}",
            activate=1,
            no_available=0,
            total_bases=20,
            dock_bikes=actual,
            free_bases=20 - actual,
            geometry=StationGeometry(type="Point", coordinates=[-3.70, 40.42]),
        )
        for sid, actual in stations
    ]
    return BicimadApiResponse(
        code="00", description="ok", datetime="2026-01-15T11:00:00", data=data
    )


def _make_bq_row(station_id: int, predicted: float, target_time: datetime) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "station_id": station_id,
        "prediction_made_at": _PRED_TS,
        "target_time": target_time,
        "predicted_dock_bikes": predicted,
        "model_version": "v20260115_100000",
    }[key]
    return row


def _patch_bq(rows: list[MagicMock]) -> MagicMock:
    mock_client = MagicMock()
    mock_client.query.return_value.__iter__ = MagicMock(return_value=iter(rows))
    mock_bq = MagicMock()
    mock_bq.Client.return_value = mock_client
    return mock_bq


# ---------------------------------------------------------------------------
# reconcile_predictions — None cases
# ---------------------------------------------------------------------------


class TestReconcilePredictionsNoData:
    def test_returns_none_when_no_bq_rows(self) -> None:
        mock_bq = _patch_bq([])
        response = _make_response([(1, 8)])
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = reconcile_predictions(response, _SNAP_TS, _PROJECT, _DATASET)
        assert result is None

    def test_returns_none_when_all_stations_missing_from_snapshot(self) -> None:
        # BQ has predictions for station 999, but snapshot only has station 1
        bq_rows = [_make_bq_row(station_id=999, predicted=10.0, target_time=_SNAP_TS)]
        mock_bq = _patch_bq(bq_rows)
        response = _make_response([(1, 8)])
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = reconcile_predictions(response, _SNAP_TS, _PROJECT, _DATASET)
        assert result is None


# ---------------------------------------------------------------------------
# reconcile_predictions — CycleMetrics correctness
# ---------------------------------------------------------------------------


class TestReconcilePredictionsCycleMetrics:
    def _run(
        self,
        bq_predictions: list[tuple[int, float]],
        actual_stations: list[tuple[int, int]],
    ) -> CycleMetrics:
        """Helper: run reconcile_predictions and assert non-None result."""
        bq_rows = [_make_bq_row(sid, pred, _SNAP_TS) for sid, pred in bq_predictions]
        mock_bq = _patch_bq(bq_rows)
        response = _make_response(actual_stations)
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = reconcile_predictions(response, _SNAP_TS, _PROJECT, _DATASET)
        assert result is not None
        return result

    def test_n_predictions_correct(self) -> None:
        metrics = self._run(
            bq_predictions=[(1, 10.0), (2, 5.0)],
            actual_stations=[(1, 8), (2, 7)],
        )
        assert metrics.n_predictions == 2

    def test_mae_computed_correctly(self) -> None:
        # station1: |10-8|=2, station2: |5-7|=2 → MAE=2.0
        metrics = self._run(
            bq_predictions=[(1, 10.0), (2, 5.0)],
            actual_stations=[(1, 8), (2, 7)],
        )
        assert metrics.mae == pytest.approx(2.0)

    def test_mae_unequal_errors(self) -> None:
        # station1: |10-8|=2, station2: |5-2|=3 → MAE=2.5
        metrics = self._run(
            bq_predictions=[(1, 10.0), (2, 5.0)],
            actual_stations=[(1, 8), (2, 2)],
        )
        assert metrics.mae == pytest.approx(2.5)

    def test_rmse_computed_correctly(self) -> None:
        # errors: 2, 4 → MSE = (4+16)/2 = 10 → RMSE = sqrt(10)
        metrics = self._run(
            bq_predictions=[(1, 10.0), (2, 5.0)],
            actual_stations=[(1, 8), (2, 9)],
        )
        assert metrics.rmse == pytest.approx(math.sqrt(10.0))

    def test_p50_error_median(self) -> None:
        # errors: 1, 2, 3 → median = 2
        metrics = self._run(
            bq_predictions=[(1, 11.0), (2, 12.0), (3, 13.0)],
            actual_stations=[(1, 10), (2, 10), (3, 10)],
        )
        assert metrics.p50_error == pytest.approx(2.0)

    def test_p90_error(self) -> None:
        # 10 errors: 1..10 → p90 = 9 (index ceil(0.9*10)-1 = 8 of sorted list)
        bq_preds = [(i, float(10 + i)) for i in range(1, 11)]
        actual = [(i, 10) for i in range(1, 11)]
        metrics = self._run(bq_predictions=bq_preds, actual_stations=actual)
        assert metrics.p90_error == pytest.approx(9.0)

    def test_worst_station_id(self) -> None:
        # station 2 has error 4, station 1 has error 2
        metrics = self._run(
            bq_predictions=[(1, 10.0), (2, 5.0)],
            actual_stations=[(1, 8), (2, 9)],
        )
        assert metrics.worst_station_id == 2
        assert metrics.worst_station_error == pytest.approx(4.0)

    def test_stations_not_in_snapshot_are_excluded(self) -> None:
        # BQ has predictions for stations 1 and 999; snapshot only has station 1
        bq_rows = [
            _make_bq_row(station_id=1, predicted=10.0, target_time=_SNAP_TS),
            _make_bq_row(station_id=999, predicted=5.0, target_time=_SNAP_TS),
        ]
        mock_bq = _patch_bq(bq_rows)
        response = _make_response([(1, 8)])
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = reconcile_predictions(response, _SNAP_TS, _PROJECT, _DATASET)
        assert result is not None
        assert result.n_predictions == 1

    def test_cycle_timestamp_equals_snapshot_timestamp(self) -> None:
        metrics = self._run(
            bq_predictions=[(1, 10.0)],
            actual_stations=[(1, 8)],
        )
        assert metrics.cycle_timestamp == _SNAP_TS

    def test_model_version_propagated(self) -> None:
        metrics = self._run(
            bq_predictions=[(1, 10.0)],
            actual_stations=[(1, 8)],
        )
        assert metrics.model_version == "v20260115_100000"

    def test_reconciled_at_has_timezone(self) -> None:
        metrics = self._run(
            bq_predictions=[(1, 10.0)],
            actual_stations=[(1, 8)],
        )
        assert metrics.reconciled_at.tzinfo is not None
