"""Shared BigQuery read helpers for the `predictions` table.

Used by both the FastAPI read API (src/serving/app.py) and the MCP server's
data layer (src/mcp/server/data_layer.py), so the query logic lives in
exactly one place instead of being duplicated or called over HTTP between
the two.
"""

from __future__ import annotations

from src.common.config import settings
from src.common.schemas import BatchPredictionRow


def load_latest_predictions() -> list[BatchPredictionRow]:
    """Load the latest batch predictions for all stations from BigQuery.

    Raises:
        ImportError: If google-cloud-bigquery is not installed.
    """
    try:
        from google.cloud import bigquery
    except ImportError as e:
        raise ImportError("Install google-cloud-bigquery for prod mode.") from e

    client = bigquery.Client(project=settings.gcp_project)
    query = f"""
        SELECT station_id, prediction_made_at, target_time,
               predicted_dock_bikes, model_version
        FROM `{settings.gcp_project}.{settings.bq_dataset}.predictions`
        WHERE DATE(prediction_made_at) = (
            SELECT MAX(DATE(prediction_made_at))
            FROM `{settings.gcp_project}.{settings.bq_dataset}.predictions`
        )
        ORDER BY prediction_made_at DESC
        LIMIT 1 OVER (PARTITION BY station_id)
    """
    return [
        BatchPredictionRow(
            station_id=row["station_id"],
            prediction_made_at=row["prediction_made_at"],
            target_time=row["target_time"],
            predicted_dock_bikes=row["predicted_dock_bikes"],
            model_version=row["model_version"],
        )
        for row in client.query(query)
    ]


def load_latest_prediction_for_station(station_id: int) -> BatchPredictionRow | None:
    """Load the most recent prediction for a single station from BigQuery.

    Filters by station_id in the query itself, instead of loading every
    station's latest prediction and filtering in Python.

    Args:
        station_id: Numeric BiciMAD station ID.

    Returns:
        The most recent BatchPredictionRow for that station, or None if no
        prediction exists for it.

    Raises:
        ImportError: If google-cloud-bigquery is not installed.
    """
    try:
        from google.cloud import bigquery
    except ImportError as e:
        raise ImportError("Install google-cloud-bigquery for prod mode.") from e

    client = bigquery.Client(project=settings.gcp_project)
    query = f"""
        SELECT station_id, prediction_made_at, target_time,
               predicted_dock_bikes, model_version
        FROM `{settings.gcp_project}.{settings.bq_dataset}.predictions`
        WHERE station_id = @station_id
        ORDER BY prediction_made_at DESC
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("station_id", "INT64", station_id)]
    )
    rows = list(client.query(query, job_config=job_config))
    if not rows:
        return None

    row = rows[0]
    return BatchPredictionRow(
        station_id=row["station_id"],
        prediction_made_at=row["prediction_made_at"],
        target_time=row["target_time"],
        predicted_dock_bikes=row["predicted_dock_bikes"],
        model_version=row["model_version"],
    )
