"""Storage layer for raw ingestion data and predictions.

Writes raw snapshots to Google Cloud Storage and BigQuery.
Also provides streaming-insert helpers for batch predictions and
aggregated cycle metrics (used by the post-ingestion predict/reconcile phases).

Partition format: station_status/dt=YYYY-MM-DD/hh=HH/mm=MM.json
"""

import json
from datetime import datetime
from typing import Any

from src.common.logging_setup import get_logger
from src.common.schemas import (
    BatchPredictionRow,
    CycleMetrics,
    OverallDailyMetrics,
    StationDailyMetrics,
)

logger = get_logger(__name__)


def _partition_key(timestamp: datetime) -> str:
    """Build the relative partition path for a snapshot timestamp.

    Args:
        timestamp: The snapshot datetime (UTC).

    Returns:
        Relative path string, e.g. ``station_status/dt=2025-06-15/hh=14/mm=30.json``.
    """
    return (
        f"station_status/"
        f"dt={timestamp.strftime('%Y-%m-%d')}/"
        f"hh={timestamp.strftime('%H')}/"
        f"mm={timestamp.strftime('%M')}.json"
    )


def write_raw_to_gcs(
    data: dict[str, Any],
    bucket: str,
    prefix: str,
    timestamp: datetime,
) -> str:
    """Write a raw snapshot dict as JSON to Google Cloud Storage.

    Args:
        data: The payload dict to serialise.
        bucket: GCS bucket name (without ``gs://``).
        prefix: Optional path prefix inside the bucket (e.g. ``raw``).
        timestamp: Snapshot timestamp used to build the partition path.

    Returns:
        Full ``gs://`` URI of the uploaded blob.
    """
    import google.cloud.storage as storage

    partition = _partition_key(timestamp)
    blob_name = f"{prefix}/{partition}" if prefix else partition

    client = storage.Client()
    bucket_obj = client.bucket(bucket)
    blob = bucket_obj.blob(blob_name)
    blob.upload_from_string(
        json.dumps(data, ensure_ascii=False, default=str),
        content_type="application/json",
    )
    gcs_uri = f"gs://{bucket}/{blob_name}"
    logger.info("Written raw snapshot to %s", gcs_uri)
    return gcs_uri


def load_to_bigquery(
    rows: list[dict[str, Any]],
    project: str,
    dataset: str,
    table: str,
) -> int:
    """Insert rows into a BigQuery table using the streaming insert API.

    Args:
        rows: List of dicts to insert (must match the table schema).
        project: GCP project id.
        dataset: BigQuery dataset name.
        table: BigQuery table name.

    Returns:
        Number of rows inserted.

    Raises:
        RuntimeError: If BigQuery reports insertion errors.
    """
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    table_ref = f"{project}.{dataset}.{table}"

    errors = client.insert_rows_json(table_ref, rows)
    if errors:
        raise RuntimeError(f"BigQuery streaming insert errors for {table_ref}: {errors}")

    logger.info("Loaded %d rows into %s", len(rows), table_ref)
    return len(rows)


def load_predictions_to_bigquery(
    predictions: list[BatchPredictionRow],
    project: str,
    dataset: str,
) -> int:
    """Insert batch prediction rows into BigQuery table `predictions`.

    Args:
        predictions: List of BatchPredictionRow objects to insert.
        project: GCP project ID.
        dataset: BigQuery dataset name.

    Returns:
        Number of rows inserted.

    Raises:
        RuntimeError: If BigQuery reports insertion errors.
    """
    rows = [p.model_dump(mode="json") for p in predictions]
    return load_to_bigquery(rows, project, dataset, "predictions")


def load_cycle_metrics_to_bigquery(
    metrics: CycleMetrics,
    project: str,
    dataset: str,
) -> int:
    """Insert one CycleMetrics row into BigQuery table `cycle_metrics`.

    Args:
        metrics: Aggregated metrics for one reconciliation cycle.
        project: GCP project ID.
        dataset: BigQuery dataset name.

    Returns:
        Number of rows inserted (always 1).

    Raises:
        RuntimeError: If BigQuery reports insertion errors.
    """
    return load_to_bigquery([metrics.model_dump(mode="json")], project, dataset, "cycle_metrics")


def load_station_daily_metrics_to_bigquery(
    metrics: list[StationDailyMetrics],
    project: str,
    dataset: str,
) -> int:
    """Insert per-station daily metrics into BigQuery table ``station_daily_metrics``.

    Args:
        metrics: List of StationDailyMetrics objects (one per station).
        project: GCP project ID.
        dataset: BigQuery dataset name.

    Returns:
        Number of rows inserted.

    Raises:
        RuntimeError: If BigQuery reports insertion errors.
    """
    rows = [m.model_dump(mode="json") for m in metrics]
    return load_to_bigquery(rows, project, dataset, "station_daily_metrics")


def load_overall_daily_metrics_to_bigquery(
    metrics: OverallDailyMetrics,
    project: str,
    dataset: str,
) -> int:
    """Insert one OverallDailyMetrics row into BigQuery table ``daily_totals``.

    Args:
        metrics: Aggregate daily metrics across all stations.
        project: GCP project ID.
        dataset: BigQuery dataset name.

    Returns:
        Number of rows inserted (always 1).

    Raises:
        RuntimeError: If BigQuery reports insertion errors.
    """
    return load_to_bigquery([metrics.model_dump(mode="json")], project, dataset, "daily_totals")
