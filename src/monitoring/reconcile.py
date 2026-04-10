"""Prediction reconciliation for BiciMAD demand forecasting.

After each ingestion cycle at time T, this module looks up predictions
that were made for T (i.e., predictions where target_time == T) and
compares them against the observed dock_bikes values in the current snapshot.

Per-station errors are computed in memory and immediately aggregated into a
single CycleMetrics row written to BigQuery ``cycle_metrics``.  No per-station
error rows are stored, keeping storage at ~35K rows/year instead of ~22M.
"""

from __future__ import annotations

import logging
import math
import statistics
from datetime import UTC, datetime

from src.common.schemas import BicimadApiResponse, CycleMetrics

logger = logging.getLogger(__name__)


def reconcile_predictions(
    current_snapshot: BicimadApiResponse,
    snapshot_timestamp: datetime,
    bq_project: str,
    bq_dataset: str,
) -> CycleMetrics | None:
    """Match predictions for target_time==snapshot_timestamp with observed values.

    Queries BigQuery for rows in the ``predictions`` table where
    ``target_time = snapshot_timestamp``.  Computes per-station absolute errors
    in memory and aggregates them into a single ``CycleMetrics`` object.

    Args:
        current_snapshot: Live BiciMAD API response for the current cycle.
        snapshot_timestamp: UTC timestamp of the current ingestion cycle.
            Predictions whose ``target_time`` equals this timestamp are reconciled.
        bq_project: GCP project ID.
        bq_dataset: BigQuery dataset name.

    Returns:
        ``CycleMetrics`` with MAE, RMSE, p50/p90, and worst station for this
        cycle, or ``None`` if no predictions exist for this target_time in BQ.
    """
    try:
        from google.cloud import bigquery
    except ImportError as e:
        raise ImportError("Install google-cloud-bigquery for reconciliation.") from e

    client = bigquery.Client(project=bq_project)

    query = f"""
        SELECT station_id, prediction_made_at, target_time,
               predicted_dock_bikes, model_version
        FROM `{bq_project}.{bq_dataset}.predictions`
        WHERE target_time = @target_time
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("target_time", "TIMESTAMP", snapshot_timestamp)
        ]
    )

    rows = list(client.query(query, job_config=job_config))
    if not rows:
        logger.info(
            "No predictions found for target_time=%s — skipping reconciliation",
            snapshot_timestamp.isoformat(),
        )
        return None

    # Build lookup: station_id → actual dock_bikes
    actual: dict[int, int] = {s.id: s.dock_bikes for s in current_snapshot.data}

    # Determine model_version from the first matched row (all rows share one version)
    model_version = str(rows[0]["model_version"])

    # Compute per-station absolute errors (in memory only — not persisted)
    abs_errors: list[float] = []
    squared_errors: list[float] = []
    worst_station_id = -1
    worst_station_error = -1.0

    for row in rows:
        station_id = int(row["station_id"])
        if station_id not in actual:
            continue  # station missing from current snapshot — skip

        predicted = float(row["predicted_dock_bikes"])
        observed = actual[station_id]
        diff = predicted - observed
        abs_err = abs(diff)

        abs_errors.append(abs_err)
        squared_errors.append(diff * diff)

        if abs_err > worst_station_error:
            worst_station_error = abs_err
            worst_station_id = station_id

    if not abs_errors:
        logger.warning(
            "All %d predictions for target_time=%s had no matching station in snapshot",
            len(rows),
            snapshot_timestamp.isoformat(),
        )
        return None

    n = len(abs_errors)
    mae = sum(abs_errors) / n
    rmse = math.sqrt(sum(squared_errors) / n)

    sorted_errors = sorted(abs_errors)
    p50 = statistics.median(sorted_errors)
    p90_idx = min(int(math.ceil(0.9 * n)) - 1, n - 1)
    p90 = sorted_errors[p90_idx]

    reconciled_at = datetime.now(tz=UTC)

    metrics = CycleMetrics(
        cycle_timestamp=snapshot_timestamp,
        model_version=model_version,
        n_predictions=n,
        mae=mae,
        rmse=rmse,
        p50_error=p50,
        p90_error=p90,
        worst_station_id=worst_station_id,
        worst_station_error=worst_station_error,
        reconciled_at=reconciled_at,
    )

    logger.info(
        "Reconciled %d/%d predictions for target_time=%s — MAE=%.4f RMSE=%.4f p90=%.4f",
        n,
        len(rows),
        snapshot_timestamp.isoformat(),
        mae,
        rmse,
        p90,
    )
    return metrics
