"""Tests for src/monitoring/daily_metrics.py."""

from datetime import date
from unittest.mock import MagicMock, patch

from src.common.schemas import OverallDailyMetrics, StationDailyMetrics
from src.monitoring.daily_metrics import (
    compute_overall_daily_metrics,
    compute_station_daily_metrics,
)

_DATE = date(2026, 1, 14)
_PROJECT = "test-project"
_DATASET = "bicimad"


def _make_station_row(
    station_id: int, model_version: str, n_cycles: int, daily_mae: float, daily_rmse: float
) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "station_id": station_id,
        "model_version": model_version,
        "n_cycles": n_cycles,
        "daily_mae": daily_mae,
        "daily_rmse": daily_rmse,
    }[key]
    return row


def _make_overall_row(
    model_version: str, n_stations: int, n_cycles: int, daily_mae: float, daily_rmse: float
) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "model_version": model_version,
        "n_stations": n_stations,
        "n_cycles": n_cycles,
        "daily_mae": daily_mae,
        "daily_rmse": daily_rmse,
    }[key]
    return row


def _patch_bq(rows: list[MagicMock]) -> MagicMock:
    mock_client = MagicMock()
    mock_client.query.return_value.__iter__ = MagicMock(return_value=iter(rows))
    mock_bq = MagicMock()
    mock_bq.Client.return_value = mock_client
    return mock_bq


# ---------------------------------------------------------------------------
# compute_station_daily_metrics
# ---------------------------------------------------------------------------


class TestComputeStationDailyMetrics:
    def test_empty_when_no_rows(self) -> None:
        mock_bq = _patch_bq([])
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = compute_station_daily_metrics(_DATE, _PROJECT, _DATASET)
        assert result == []

    def test_returns_one_row_per_station(self) -> None:
        rows = [
            _make_station_row(1, "v1", 4, 2.0, 2.5),
            _make_station_row(2, "v1", 4, 3.0, 3.5),
        ]
        mock_bq = _patch_bq(rows)
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = compute_station_daily_metrics(_DATE, _PROJECT, _DATASET)
        assert len(result) == 2

    def test_result_type_is_station_daily_metrics(self) -> None:
        rows = [_make_station_row(1, "v1", 4, 2.0, 2.5)]
        mock_bq = _patch_bq(rows)
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = compute_station_daily_metrics(_DATE, _PROJECT, _DATASET)
        assert isinstance(result[0], StationDailyMetrics)

    def test_date_field_matches_argument(self) -> None:
        rows = [_make_station_row(1, "v1", 4, 2.0, 2.5)]
        mock_bq = _patch_bq(rows)
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = compute_station_daily_metrics(_DATE, _PROJECT, _DATASET)
        assert result[0].date == _DATE

    def test_fields_correct(self) -> None:
        rows = [_make_station_row(42, "v20260115", 8, 1.5, 2.0)]
        mock_bq = _patch_bq(rows)
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = compute_station_daily_metrics(_DATE, _PROJECT, _DATASET)
        m = result[0]
        assert m.station_id == 42
        assert m.model_version == "v20260115"
        assert m.n_cycles == 8
        assert m.daily_mae == 1.5
        assert m.daily_rmse == 2.0


# ---------------------------------------------------------------------------
# compute_overall_daily_metrics
# ---------------------------------------------------------------------------


class TestComputeOverallDailyMetrics:
    def test_none_when_no_rows(self) -> None:
        mock_bq = _patch_bq([])
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = compute_overall_daily_metrics(_DATE, _PROJECT, _DATASET)
        assert result is None

    def test_returns_overall_daily_metrics(self) -> None:
        rows = [_make_overall_row("v1", 10, 40, 2.0, 2.5)]
        mock_bq = _patch_bq(rows)
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = compute_overall_daily_metrics(_DATE, _PROJECT, _DATASET)
        assert isinstance(result, OverallDailyMetrics)

    def test_aggregates_all_stations(self) -> None:
        rows = [_make_overall_row("v1", 5, 20, 1.8, 2.2)]
        mock_bq = _patch_bq(rows)
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = compute_overall_daily_metrics(_DATE, _PROJECT, _DATASET)
        assert result is not None
        assert result.n_stations == 5
        assert result.n_cycles == 20

    def test_date_field_matches_argument(self) -> None:
        rows = [_make_overall_row("v1", 3, 12, 1.0, 1.2)]
        mock_bq = _patch_bq(rows)
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = compute_overall_daily_metrics(_DATE, _PROJECT, _DATASET)
        assert result is not None
        assert result.date == _DATE

    def test_mae_and_rmse_correct(self) -> None:
        rows = [_make_overall_row("v1", 2, 8, 3.14, 4.28)]
        mock_bq = _patch_bq(rows)
        with patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}):
            result = compute_overall_daily_metrics(_DATE, _PROJECT, _DATASET)
        assert result is not None
        assert result.daily_mae == 3.14
        assert result.daily_rmse == 4.28
