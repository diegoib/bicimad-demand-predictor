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

from src.common.logging_setup import setup_logging
from src.common.schemas import BatchPredictionRow
from src.serving.predictions_query import (
    load_latest_prediction_for_station,
    load_latest_predictions,
)

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="BiciMAD Demand Forecast API",
    description="Read API for pre-computed dock_bikes predictions (t+1h) per station.",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness check. Always returns 200."""
    try:
        rows = load_latest_predictions()
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
        return load_latest_predictions()
    except Exception as e:
        raise HTTPException(status_code=503, detail="No predictions available yet") from e


@app.get("/predictions/{station_id}", response_model=BatchPredictionRow)
def predictions_station(station_id: int) -> BatchPredictionRow:
    """Return the latest prediction for a single station.

    Args:
        station_id: Numeric BiciMAD station ID.

    Raises:
        503: If BigQuery could not be queried.
        404: If station_id has no prediction in the latest batch.
    """
    try:
        row = load_latest_prediction_for_station(station_id)
    except Exception as e:
        raise HTTPException(status_code=503, detail="No predictions available yet") from e

    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Station {station_id} not found in latest predictions"
        )
    return row
