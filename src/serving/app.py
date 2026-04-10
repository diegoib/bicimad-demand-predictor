"""FastAPI read API for BiciMAD demand forecasting.

Exposes pre-computed batch predictions written by the ingestion pipeline
(src/ingestion/main.py → predict_all_stations → write_predictions_to_bigquery).

Endpoints:
    GET /health                    — Liveness check (always 200)
    GET /predictions/latest        — Latest predictions for all stations
    GET /predictions/{station_id}  — Latest prediction for one station

BigQuery source:
    Reads from the `predictions` table, most recent prediction_made_at.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException

from src.common.config import settings
from src.common.logging_setup import setup_logging
from src.common.schemas import BatchPredictionRow

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="BiciMAD Demand Forecast API",
    description="Read API for pre-computed dock_bikes predictions (t+1h) per station.",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _load_latest_bigquery() -> list[BatchPredictionRow]:
    """Load latest predictions from BigQuery `predictions` table.

    Raises:
        ImportError: If google-cloud-bigquery is not installed.
    """
    try:
        from google.cloud import bigquery
    except ImportError as e:
        raise ImportError("Install google-cloud-bigquery for prod mode.") from e

    client = bigquery.Client(project=settings.bq_project)
    query = f"""
        SELECT station_id, prediction_made_at, target_time,
               predicted_dock_bikes, model_version
        FROM `{settings.bq_project}.{settings.bq_dataset}.predictions`
        WHERE DATE(prediction_made_at) = (
            SELECT MAX(DATE(prediction_made_at))
            FROM `{settings.bq_project}.{settings.bq_dataset}.predictions`
        )
        ORDER BY prediction_made_at DESC
        LIMIT 1 OVER (PARTITION BY station_id)
    """
    rows = []
    for row in client.query(query):
        rows.append(
            BatchPredictionRow(
                station_id=row["station_id"],
                prediction_made_at=row["prediction_made_at"],
                target_time=row["target_time"],
                predicted_dock_bikes=row["predicted_dock_bikes"],
                model_version=row["model_version"],
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness check. Always returns 200."""
    try:
        rows = _load_latest_bigquery()
        predictions_available = len(rows)
        latest_ts: datetime | None = rows[0].prediction_made_at if rows else None
    except Exception:
        predictions_available = 0
        latest_ts = None

    return {
        "status": "ok",
        "predictions_available": predictions_available,
        "latest_snapshot": latest_ts.isoformat() if latest_ts else None,
    }


@app.get("/predictions/latest", response_model=list[BatchPredictionRow])
def predictions_latest() -> list[BatchPredictionRow]:
    """Return the latest batch predictions for all stations.

    Raises:
        503: If no predictions have been written yet.
    """
    try:
        return _load_latest_bigquery()
    except Exception as e:
        raise HTTPException(status_code=503, detail="No predictions available yet") from e


@app.get("/predictions/{station_id}", response_model=BatchPredictionRow)
def predictions_station(station_id: int) -> BatchPredictionRow:
    """Return the latest prediction for a single station.

    Args:
        station_id: Numeric BiciMAD station ID.

    Raises:
        503: If no predictions have been written yet.
        404: If station_id has no prediction in the latest batch.
    """
    try:
        rows = _load_latest_bigquery()
    except Exception as e:
        raise HTTPException(status_code=503, detail="No predictions available yet") from e

    for row in rows:
        if row.station_id == station_id:
            return row

    raise HTTPException(
        status_code=404, detail=f"Station {station_id} not found in latest predictions"
    )
