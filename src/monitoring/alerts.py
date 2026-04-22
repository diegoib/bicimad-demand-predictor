"""Performance and data drift alert checks.

Two alert functions:
- ``check_performance_alert``: compares MAE from the last 24 h of
  ``cycle_metrics`` against the training MAE stored in model metadata.
  Fires (returns True) if online MAE > training MAE × 1.20.

- ``check_drift_alert``: evaluates the drift summary dict returned by
  ``drift_report.generate_daily_drift_report``.  Fires if more than 30 %
  of features have drifted.

Invoked by the ``daily_monitoring_dag`` (06:05 UTC) via::

    python -m src.monitoring.alerts [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.common.config import settings
from src.common.logging_setup import get_logger, setup_logging
from src.training.registry import get_prod_model_metrics

logger = get_logger(__name__)

_PERFORMANCE_THRESHOLD = 1.20  # alert if online MAE > training MAE × this factor
_DRIFT_THRESHOLD = 0.30  # alert if more than 30 % of features drift


def check_performance_alert(bq_project: str, bq_dataset: str) -> bool:
    """Return True if the rolling 24-h MAE exceeds 120 % of the training MAE.

    Queries ``cycle_metrics`` for the last 24 hours.  If no rows exist
    (e.g., reconciliation has never run), returns False silently.

    Args:
        bq_project: GCP project ID.
        bq_dataset: BigQuery dataset name.

    Returns:
        True if an alert condition is met, False otherwise.
    """
    try:
        from google.cloud import bigquery
    except ImportError as e:
        raise ImportError("Install google-cloud-bigquery for performance alerts.") from e

    # ------------------------------------------------------------------
    # Query online MAE (last 24 h)
    # ------------------------------------------------------------------
    client = bigquery.Client(project=bq_project)
    query = f"""
        SELECT AVG(mae) AS avg_mae
        FROM `{bq_project}.{bq_dataset}.cycle_metrics`
        WHERE cycle_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
    """
    rows = list(client.query(query))

    if not rows or rows[0]["avg_mae"] is None:
        logger.info("No cycle_metrics data in last 24 h — skipping performance alert")
        return False

    online_mae: float = float(rows[0]["avg_mae"])

    # ------------------------------------------------------------------
    # Load training MAE from the current @prod model in MLflow
    # ------------------------------------------------------------------
    prod = get_prod_model_metrics()
    if prod is None:
        logger.warning("No @prod model in MLflow registry — skipping performance alert")
        return False

    training_mae: float = float(prod.get("mae", float("nan")))
    if not (training_mae > 0):
        logger.warning(
            "Invalid training MAE in metadata: %s — skipping performance alert", training_mae
        )
        return False

    threshold = training_mae * _PERFORMANCE_THRESHOLD
    if online_mae > threshold:
        logger.warning(
            "PERFORMANCE ALERT: online MAE=%.4f exceeds threshold=%.4f (training MAE=%.4f × %.2f)",
            online_mae,
            threshold,
            training_mae,
            _PERFORMANCE_THRESHOLD,
        )
        return True

    logger.info(
        "Performance OK: online MAE=%.4f (threshold=%.4f, training MAE=%.4f)",
        online_mae,
        threshold,
        training_mae,
    )
    return False


def check_drift_alert(drift_summary: dict[str, Any]) -> bool:
    """Return True if the share of drifted features exceeds the threshold.

    Args:
        drift_summary: Dict returned by ``generate_daily_drift_report``.
            Must contain a ``share_drifted`` key (float 0-1).

    Returns:
        True if ``share_drifted`` > 0.30, False otherwise.
    """
    share = float(drift_summary.get("share_drifted", 0.0))
    if share > _DRIFT_THRESHOLD:
        drifted = drift_summary.get("drifted_feature_names", [])
        logger.warning(
            "DRIFT ALERT: %.1f%% of features drifted (threshold=%.0f%%): %s",
            share * 100,
            _DRIFT_THRESHOLD * 100,
            drifted,
        )
        return True

    logger.info(
        "Drift OK: share_drifted=%.1f%% (threshold=%.0f%%)", share * 100, _DRIFT_THRESHOLD * 100
    )
    return False


# ---------------------------------------------------------------------------
# Entry point (called by the Airflow BashOperator)
# ---------------------------------------------------------------------------


def _yesterday_utc() -> date:
    return (datetime.now(UTC) - timedelta(days=1)).date()


def _load_drift_summary_from_gcs(target_date: date) -> dict[str, Any]:
    """Download the JSON drift summary written by drift_report.py."""
    try:
        import google.cloud.storage as gcs

        client = gcs.Client(project=settings.gcp_project)
        bucket = client.bucket(settings.gcs_bucket)
        blob_name = f"monitoring/drift/{target_date}_summary.json"
        blob = bucket.blob(blob_name)
        raw = blob.download_as_text()
        result: dict[str, Any] = json.loads(raw)
        return result
    except Exception as exc:
        logger.warning("Could not load drift summary for %s from GCS: %s", target_date, exc)
        return {}


if __name__ == "__main__":
    setup_logging()

    parser = argparse.ArgumentParser(description="Run monitoring alerts")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=_yesterday_utc(),
        help="Date whose drift report to check (YYYY-MM-DD). Defaults to yesterday UTC.",
    )
    args = parser.parse_args()
    target: date = args.date

    logger.info("Running alert checks for %s", target)

    perf_fired = check_performance_alert(settings.gcp_project, settings.bq_dataset)
    drift_summary = _load_drift_summary_from_gcs(target)
    drift_fired = check_drift_alert(drift_summary)

    if perf_fired or drift_fired:
        logger.warning("One or more alerts fired for %s — investigation required", target)
    else:
        logger.info("All checks passed for %s", target)
