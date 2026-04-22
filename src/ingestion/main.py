"""Ingestion orchestrator.

Ties together authentication, data fetching, validation, storage, batch
prediction and prediction reconciliation into a single ``ingest()`` function
that can be called:

- By Airflow (via BashOperator spawning ``python -m src.ingestion.main``).
- As a Cloud Function entry point via ``handler(request)``.
- Directly from the command line.

Pipeline phases (in order):
  1. Authenticate with EMT API (token cached on disk)
  2. Fetch station snapshots + weather
  3. Write raw payload to GCS
  4. Stream station rows to BigQuery ``station_status_raw``
  5. Run batch inference → write predictions to BigQuery ``predictions``
     (try/except: failure here does NOT abort the ingestion cycle)
  6. Reconcile previous predictions against current actuals → write
     aggregated CycleMetrics to BigQuery ``cycle_metrics``
     (try/except: failure here does NOT abort the ingestion cycle)
"""

import json
from datetime import UTC, datetime
from typing import Any

from src.common.config import settings
from src.common.logging_setup import get_logger, setup_logging
from src.ingestion.bicimad_client import TokenCache, fetch_stations, get_valid_token
from src.ingestion.storage import (
    load_cycle_metrics_to_bigquery,
    load_predictions_to_bigquery,
    load_to_bigquery,
    write_raw_to_gcs,
)
from src.ingestion.weather_client import fetch_current_weather

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level model cache — survives between Airflow task retries within the
# same subprocess (BashOperator), but NOT between separate Python invocations.
# Calling _get_model() downloads once from GCS on first use, then reuses.
# ---------------------------------------------------------------------------

_model_cache: tuple[Any, dict[str, Any]] | None = None


def _get_model() -> tuple[Any, dict[str, Any]]:
    """Return the cached model, loading the @prod alias from MLflow on first call."""
    global _model_cache
    if _model_cache is None:
        from src.training.registry import load_prod_model

        _model_cache = load_prod_model()
    return _model_cache


def ingest() -> dict[str, Any]:
    """Run one full ingestion cycle: fetch → store → predict → reconcile.

    Phases 5 (predict) and 6 (reconcile) are non-fatal: failures are logged
    as warnings but do not raise, so the Airflow task succeeds even when no
    trained model exists yet.

    Returns:
        Summary dict with status, stations count, timestamp, and prediction
        and reconciliation stats.

    Raises:
        Exception: Propagates unhandled errors from phases 1–4.
    """
    setup_logging()
    logger.info("Starting ingestion cycle")

    timestamp = datetime.now(tz=UTC).replace(second=0, microsecond=0)

    # ------------------------------------------------------------------
    # 1. Authenticate (token cached on disk — survives between Airflow runs)
    # ------------------------------------------------------------------
    cache = TokenCache()
    access_token = get_valid_token(cache)

    # ------------------------------------------------------------------
    # 2. Fetch data
    # ------------------------------------------------------------------
    stations_response = fetch_stations(access_token)

    try:
        weather = fetch_current_weather()
    except Exception as exc:
        logger.warning("Open-Meteo unavailable, weather will be null: %s", exc)
        weather = None

    logger.info("Fetched %d stations", len(stations_response.data))

    # ------------------------------------------------------------------
    # 3. Build combined raw payload
    # ------------------------------------------------------------------
    payload: dict[str, Any] = {
        "ingestion_timestamp": timestamp.isoformat(),
        "stations": stations_response.model_dump(mode="json"),
        "weather": weather.model_dump(mode="json") if weather is not None else None,
    }

    # ------------------------------------------------------------------
    # 4. Write to GCS + BigQuery
    # ------------------------------------------------------------------
    write_raw_to_gcs(payload, settings.gcs_bucket, "raw", timestamp)

    rows: list[dict[str, Any]] = []
    weather_dict = weather.model_dump(mode="json") if weather is not None else None
    for station in stations_response.data:
        row = station.model_dump(mode="json")
        row["ingestion_timestamp"] = timestamp.isoformat()
        row["weather_snapshot"] = weather_dict
        rows.append(row)

    load_to_bigquery(rows, settings.gcp_project, settings.bq_dataset, "station_status_raw")

    # ------------------------------------------------------------------
    # 5. Batch inference (non-fatal)
    # ------------------------------------------------------------------
    predictions_written = 0
    try:
        from src.features.build_dataset import _load_bigquery_snapshots
        from src.serving.predict import predict_all_stations

        model, metadata = _get_model()
        model_version = str(metadata.get("version", "unknown"))

        # Load 8 days of historical snapshots to populate lag and rolling
        # features.
        from datetime import timedelta

        hist_start = (timestamp - timedelta(days=8)).date()
        hist_end = timestamp.date()
        try:
            historical_df = _load_bigquery_snapshots(hist_start, hist_end)
        except Exception as hist_exc:
            logger.warning("Could not load historical snapshots for features: %s", hist_exc)
            historical_df = None

        predictions = predict_all_stations(
            model,
            model_version,
            stations_response,
            weather,
            timestamp,
            historical_df=historical_df,
        )
        if predictions:
            predictions_written = load_predictions_to_bigquery(
                predictions, settings.gcp_project, settings.bq_dataset
            )
            logger.info("Wrote %d predictions to BigQuery", predictions_written)
        else:
            logger.info("No predictions produced (no active stations or model not ready)")
    except Exception as exc:
        logger.warning("Prediction phase failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # 6. Reconciliation (non-fatal)
    # ------------------------------------------------------------------
    cycle_mae: float | None = None
    try:
        from src.monitoring.reconcile import reconcile_predictions

        metrics = reconcile_predictions(
            stations_response, timestamp, settings.gcp_project, settings.bq_dataset
        )
        if metrics:
            load_cycle_metrics_to_bigquery(metrics, settings.gcp_project, settings.bq_dataset)
            cycle_mae = metrics.mae
            logger.info(
                "Reconciliation complete: n=%d MAE=%.4f RMSE=%.4f p90=%.4f",
                metrics.n_predictions,
                metrics.mae,
                metrics.rmse,
                metrics.p90_error,
            )
    except Exception as exc:
        logger.warning("Reconciliation phase failed (non-fatal): %s", exc)

    result: dict[str, Any] = {
        "status": "ok",
        "stations": len(stations_response.data),
        "timestamp": timestamp.isoformat(),
        "weather_available": weather is not None,
        "predictions_written": predictions_written,
        "cycle_mae": cycle_mae,
    }
    logger.info("Ingestion cycle complete: %s", result)
    return result


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def handler(request: object) -> tuple[str, int]:
    """Cloud Function HTTP entry point.

    Args:
        request: The Flask/functions-framework Request object (unused).

    Returns:
        Tuple of (JSON body, HTTP status code).
    """
    try:
        result = ingest()
        return json.dumps(result), 200
    except Exception as exc:
        logger.exception("Ingestion failed: %s", exc)
        return json.dumps({"status": "error", "error": str(exc)}), 500


if __name__ == "__main__":
    import sys

    outcome = ingest()
    print(json.dumps(outcome, indent=2))
    sys.exit(0 if outcome.get("status") == "ok" else 1)
