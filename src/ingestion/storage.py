"""Storage layer for raw ingestion data.

Supports:
- Local filesystem (dev mode): JSON files partitioned by date/hour/minute.
- Google Cloud Storage (prod mode): same partition scheme under a GCS prefix.
- BigQuery (prod mode): incremental row inserts.

Partition format: station_status/dt=YYYY-MM-DD/hh=HH/mm=MM.json
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.common.logging_setup import get_logger

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


def write_raw_to_local(
    data: dict[str, Any],
    base_path: str,
    timestamp: datetime,
) -> Path:
    """Write a raw snapshot dict to a local JSON file.

    Creates parent directories as needed.

    Args:
        data: The payload dict to serialise.
        base_path: Root directory for local storage (e.g. ``data/raw``).
        timestamp: Snapshot timestamp used to build the partition path.

    Returns:
        Absolute Path to the written file.
    """
    output_path = Path(base_path) / _partition_key(timestamp)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Written raw snapshot to %s", output_path)
    return output_path


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
    from google.cloud import storage  # type: ignore[attr-defined]

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
