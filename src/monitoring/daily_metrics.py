"""Daily aggregation of per-station prediction error metrics.

Reads from BigQuery tables ``predictions`` and ``station_status_raw`` for a
given date, computes MAE/RMSE per station (and overall), and returns the
results as Pydantic objects ready for insertion into ``station_daily_metrics``
and ``daily_totals``.

Invoked by the ``daily_monitoring_dag`` (06:05 UTC) via::

    python -m src.monitoring.daily_metrics [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import math
from datetime import UTC, date, datetime, timedelta

from src.common.config import settings
from src.common.logging_setup import get_logger, setup_logging
from src.common.schemas import OverallDailyMetrics, StationDailyMetrics
from src.ingestion.storage import (
    load_overall_daily_metrics_to_bigquery,
    load_station_daily_metrics_to_bigquery,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# BigQuery queries
# ---------------------------------------------------------------------------

_STATION_QUERY = """
    SELECT
        p.station_id,
        p.model_version,
        COUNT(*) AS n_cycles,
        AVG(ABS(p.predicted_dock_bikes - s.dock_bikes)) AS daily_mae,
        SQRT(AVG(POW(p.predicted_dock_bikes - s.dock_bikes, 2))) AS daily_rmse
    FROM `{project}.{dataset}.predictions` p
    JOIN `{project}.{dataset}.station_status_raw` s
      ON p.station_id = s.id
     AND p.target_time = s.ingestion_timestamp
    WHERE DATE(p.target_time) = @target_date
    GROUP BY p.station_id, p.model_version
"""

_OVERALL_QUERY = """
    SELECT
        p.model_version,
        COUNT(DISTINCT p.station_id) AS n_stations,
        COUNT(*) AS n_cycles,
        AVG(ABS(p.predicted_dock_bikes - s.dock_bikes)) AS daily_mae,
        SQRT(AVG(POW(p.predicted_dock_bikes - s.dock_bikes, 2))) AS daily_rmse
    FROM `{project}.{dataset}.predictions` p
    JOIN `{project}.{dataset}.station_status_raw` s
      ON p.station_id = s.id
     AND p.target_time = s.ingestion_timestamp
    WHERE DATE(p.target_time) = @target_date
    GROUP BY p.model_version
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_station_daily_metrics(
    target_date: date,
    bq_project: str,
    bq_dataset: str,
) -> list[StationDailyMetrics]:
    """Compute per-station MAE/RMSE for all reconciled cycles on *target_date*.

    Args:
        target_date: The UTC calendar date to aggregate (typically yesterday).
        bq_project: GCP project ID.
        bq_dataset: BigQuery dataset name.

    Returns:
        List of ``StationDailyMetrics`` objects (one per station).
        Empty list if no predictions were reconciled on that date.
    """
    try:
        from google.cloud import bigquery
    except ImportError as e:
        raise ImportError("Install google-cloud-bigquery for daily metrics.") from e

    client = bigquery.Client(project=bq_project)
    query = _STATION_QUERY.format(project=bq_project, dataset=bq_dataset)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("target_date", "DATE", target_date.isoformat())
        ]
    )

    results: list[StationDailyMetrics] = []
    for row in client.query(query, job_config=job_config):
        results.append(
            StationDailyMetrics(
                date=target_date,
                station_id=int(row["station_id"]),
                model_version=str(row["model_version"]),
                n_cycles=int(row["n_cycles"]),
                daily_mae=float(row["daily_mae"]),
                daily_rmse=float(row["daily_rmse"]),
            )
        )

    logger.info(
        "Computed station daily metrics for %s: %d stations",
        target_date.isoformat(),
        len(results),
    )
    return results


def compute_overall_daily_metrics(
    target_date: date,
    bq_project: str,
    bq_dataset: str,
) -> OverallDailyMetrics | None:
    """Compute aggregate MAE/RMSE across all stations for *target_date*.

    Args:
        target_date: The UTC calendar date to aggregate.
        bq_project: GCP project ID.
        bq_dataset: BigQuery dataset name.

    Returns:
        A single ``OverallDailyMetrics`` object, or ``None`` if no data.
    """
    try:
        from google.cloud import bigquery
    except ImportError as e:
        raise ImportError("Install google-cloud-bigquery for daily metrics.") from e

    client = bigquery.Client(project=bq_project)
    query = _OVERALL_QUERY.format(project=bq_project, dataset=bq_dataset)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("target_date", "DATE", target_date.isoformat())
        ]
    )

    rows = list(client.query(query, job_config=job_config))
    if not rows:
        logger.info(
            "No reconciled predictions found for %s — skipping overall metrics", target_date
        )
        return None

    row = rows[0]
    metrics = OverallDailyMetrics(
        date=target_date,
        model_version=str(row["model_version"]),
        n_stations=int(row["n_stations"]),
        n_cycles=int(row["n_cycles"]),
        daily_mae=float(row["daily_mae"]),
        daily_rmse=float(row["daily_rmse"]),
    )
    logger.info(
        "Overall daily metrics for %s: n_stations=%d mae=%.4f rmse=%.4f",
        target_date.isoformat(),
        metrics.n_stations,
        metrics.daily_mae,
        metrics.daily_rmse,
    )
    return metrics


# ---------------------------------------------------------------------------
# Entry point (called by the Airflow BashOperator)
# ---------------------------------------------------------------------------


def _yesterday_utc() -> date:
    return (datetime.now(UTC) - timedelta(days=1)).date()


if __name__ == "__main__":
    setup_logging()

    parser = argparse.ArgumentParser(description="Compute daily prediction error metrics")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=_yesterday_utc(),
        help="Date to process (YYYY-MM-DD). Defaults to yesterday UTC.",
    )
    args = parser.parse_args()
    target: date = args.date

    logger.info("Running daily metrics for %s", target)

    station_metrics = compute_station_daily_metrics(
        target, settings.bq_project, settings.bq_dataset
    )
    if station_metrics:
        n = load_station_daily_metrics_to_bigquery(
            station_metrics, settings.bq_project, settings.bq_dataset
        )
        logger.info("Wrote %d station daily metric rows to BQ", n)
    else:
        logger.info("No station metrics to write for %s", target)

    overall = compute_overall_daily_metrics(target, settings.bq_project, settings.bq_dataset)
    if overall:
        load_overall_daily_metrics_to_bigquery(overall, settings.bq_project, settings.bq_dataset)
        logger.info(
            "Wrote overall daily metrics for %s: mae=%.4f rmse=%.4f",
            target,
            overall.daily_mae,
            overall.daily_rmse,
        )
    else:
        logger.info("No overall metrics to write for %s", target)

    # Validate that math is consistent: overall.daily_mae should be positive
    if overall and not math.isfinite(overall.daily_mae):
        raise ValueError(f"Non-finite daily_mae for {target}: {overall.daily_mae}")
