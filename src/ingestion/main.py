"""Ingestion orchestrator.

Ties together authentication, data fetching, validation, and storage into a
single ``ingest()`` function that can be called:

- By Airflow (imported and called from a DAG task).
- As a Cloud Function entry point via ``handler(request)``.
- Directly from the command line for local testing.
"""

import json
from datetime import UTC, datetime
from typing import Any

from src.common.config import settings
from src.common.logging_setup import get_logger, setup_logging
from src.ingestion.bicimad_client import TokenCache, fetch_stations, get_valid_token
from src.ingestion.storage import load_to_bigquery, write_raw_to_gcs, write_raw_to_local
from src.ingestion.weather_client import fetch_current_weather

logger = get_logger(__name__)


def ingest() -> dict[str, Any]:
    """Run one ingestion cycle: authenticate → fetch → validate → write.

    In dev mode (``BICIMAD_ENV=dev``) data is written to local files under
    ``local_data_dir``.  In prod mode data goes to GCS and BigQuery.

    Returns:
        Summary dict with ``status``, ``stations`` count and ``timestamp``.

    Raises:
        Exception: Propagates any unhandled error from the sub-components.
    """
    setup_logging(settings.env)
    logger.info("Starting ingestion cycle (env=%s)", settings.env)

    timestamp = datetime.now(tz=UTC)

    # ------------------------------------------------------------------
    # 1. Authenticate
    # ------------------------------------------------------------------
    cache = TokenCache() if settings.env == "dev" else None
    access_token = get_valid_token(cache)

    # ------------------------------------------------------------------
    # 2. Fetch data
    # ------------------------------------------------------------------
    stations_response = fetch_stations(access_token)
    weather = fetch_current_weather()

    logger.info("Fetched %d stations", len(stations_response.data))

    # ------------------------------------------------------------------
    # 3. Build combined raw payload
    # ------------------------------------------------------------------
    payload: dict[str, Any] = {
        "ingestion_timestamp": timestamp.isoformat(),
        "stations": stations_response.model_dump(mode="json"),
        "weather": weather.model_dump(mode="json"),
    }

    # ------------------------------------------------------------------
    # 4. Write
    # ------------------------------------------------------------------
    if settings.env == "dev":
        write_raw_to_local(payload, settings.local_data_dir, timestamp)
    else:
        write_raw_to_gcs(payload, settings.gcs_bucket, "raw", timestamp)

        # Flatten station rows for BigQuery streaming insert
        rows: list[dict[str, Any]] = []
        weather_dict = weather.model_dump(mode="json")
        for station in stations_response.data:
            row = station.model_dump(mode="json")
            row["ingestion_timestamp"] = timestamp.isoformat()
            row["weather_snapshot"] = weather_dict
            rows.append(row)

        load_to_bigquery(rows, settings.bq_project, settings.bq_dataset, "station_status_raw")

    result: dict[str, Any] = {
        "status": "ok",
        "stations": len(stations_response.data),
        "timestamp": timestamp.isoformat(),
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
