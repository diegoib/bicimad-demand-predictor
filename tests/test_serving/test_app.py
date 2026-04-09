"""Tests for the BiciMAD serving read API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import src.serving.app as app_module
from src.common.schemas import BatchPredictionRow
from src.serving.app import app

client = TestClient(app)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SNAP_TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_TARGET_TS = _SNAP_TS + timedelta(hours=1)

_ROWS_PARSED: list[BatchPredictionRow] = [
    BatchPredictionRow(
        station_id=1,
        prediction_made_at=_SNAP_TS,
        target_time=_TARGET_TS,
        predicted_dock_bikes=5.3,
        model_version="v20260101_120000",
    ),
    BatchPredictionRow(
        station_id=2,
        prediction_made_at=_SNAP_TS,
        target_time=_TARGET_TS,
        predicted_dock_bikes=8.1,
        model_version="v20260101_120000",
    ),
]


@pytest.fixture()  # type: ignore[misc]
def patched_client() -> TestClient:
    """Client with _load_latest_bigquery mocked to return sample predictions."""
    with patch.object(app_module, "_load_latest_bigquery", return_value=_ROWS_PARSED):
        yield TestClient(app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_always_200() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_no_predictions() -> None:
    body = client.get("/health").json()
    assert body["predictions_available"] == 0
    assert body["latest_snapshot"] is None


def test_health_with_predictions(patched_client: TestClient) -> None:
    body = patched_client.get("/health").json()
    assert body["predictions_available"] == 2
    assert body["latest_snapshot"] is not None


# ---------------------------------------------------------------------------
# GET /predictions/latest
# ---------------------------------------------------------------------------


def test_predictions_latest_no_data_returns_503() -> None:
    resp = client.get("/predictions/latest")
    assert resp.status_code == 503


def test_predictions_latest_returns_all_stations(patched_client: TestClient) -> None:
    resp = patched_client.get("/predictions/latest")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2


def test_predictions_latest_schema(patched_client: TestClient) -> None:
    body = patched_client.get("/predictions/latest").json()
    row = body[0]
    assert "station_id" in row
    assert "prediction_made_at" in row
    assert "target_time" in row
    assert "predicted_dock_bikes" in row
    assert "model_version" in row


def test_predictions_latest_target_time_is_one_hour_ahead(patched_client: TestClient) -> None:
    body = patched_client.get("/predictions/latest").json()
    for row in body:
        made_at = datetime.fromisoformat(row["prediction_made_at"])
        target = datetime.fromisoformat(row["target_time"])
        assert target - made_at == timedelta(hours=1)


def test_predictions_latest_values(patched_client: TestClient) -> None:
    body = patched_client.get("/predictions/latest").json()
    station_ids = {r["station_id"] for r in body}
    assert station_ids == {1, 2}
    preds = {r["station_id"]: r["predicted_dock_bikes"] for r in body}
    assert preds[1] == pytest.approx(5.3)
    assert preds[2] == pytest.approx(8.1)


# ---------------------------------------------------------------------------
# GET /predictions/{station_id}
# ---------------------------------------------------------------------------


def test_predictions_station_no_data_returns_503() -> None:
    resp = client.get("/predictions/1")
    assert resp.status_code == 503


def test_predictions_station_not_found_returns_404(patched_client: TestClient) -> None:
    resp = patched_client.get("/predictions/9999")
    assert resp.status_code == 404


def test_predictions_station_valid(patched_client: TestClient) -> None:
    resp = patched_client.get("/predictions/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["station_id"] == 1
    assert body["predicted_dock_bikes"] == pytest.approx(5.3)
    assert body["model_version"] == "v20260101_120000"


def test_predictions_station_target_time(patched_client: TestClient) -> None:
    body = patched_client.get("/predictions/1").json()
    made_at = datetime.fromisoformat(body["prediction_made_at"])
    target = datetime.fromisoformat(body["target_time"])
    assert target - made_at == timedelta(hours=1)
