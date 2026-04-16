"""Daily data drift report using Evidently.

Compares feature distributions of the previous day's data against the
training reference window (train_days) used to train the active model.  Generates an HTML
report and uploads it to GCS together with a lightweight JSON summary that
the dashboard and alert checks can read without loading the full HTML.

Invoked by the ``daily_monitoring_dag`` (06:05 UTC) via::

    python -m src.monitoring.drift_report [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from src.common.config import settings
from src.common.logging_setup import get_logger, setup_logging

logger = get_logger(__name__)

# GCS paths
_DRIFT_HTML_PREFIX = "monitoring/drift"
_DRIFT_SUMMARY_PREFIX = "monitoring/drift"

# Feature columns to include in the drift report (exclude identifiers and target)
_EXCLUDE_COLS = {"station_id", "snapshot_timestamp", "target_dock_bikes_1h"}


def _feature_cols(df: Any) -> list[str]:
    return [c for c in df.columns if c not in _EXCLUDE_COLS]


def generate_daily_drift_report(
    target_date: date,
    bq_project: str,
    bq_dataset: str,
    gcs_bucket: str,
) -> dict[str, Any]:
    """Compare feature distributions for *target_date* against the training reference window.

    Args:
        target_date: Date whose data is used as the "current" window.
        bq_project: GCP project ID.
        bq_dataset: BigQuery dataset name.
        gcs_bucket: GCS bucket name (without ``gs://``).

    Returns:
        Summary dict with keys:
        ``n_drifted_features``, ``share_drifted``, ``drifted_feature_names``.
        Returns zeros/empty if no data is available for *target_date*.
    """
    from src.features.build_dataset import _load_bigquery_snapshots
    from src.features.build_features import build_all_features
    from src.training.registry import load_latest_metadata

    _empty: dict[str, Any] = {
        "n_drifted_features": 0,
        "share_drifted": 0.0,
        "drifted_feature_names": [],
    }

    # ------------------------------------------------------------------
    # 1. Load current day's features
    # ------------------------------------------------------------------
    # Load warmup_days of prior data so rolling/lag features (e.g. avg_dock_same_hour_7d)
    # have enough history. After building features, filter down to target_date only.
    cur_start = target_date - timedelta(days=settings.feature_warmup_days)
    try:
        cur_polars = _load_bigquery_snapshots(cur_start, target_date)
    except Exception as exc:
        logger.warning(
            "Could not load snapshots for %s: %s — skipping drift report", target_date, exc
        )
        return _empty

    if cur_polars is None or cur_polars.is_empty():
        logger.info("No snapshot data for %s — skipping drift report", target_date)
        return _empty

    cur_featured = build_all_features(cur_polars)
    # Keep only rows from target_date after feature engineering
    cur_featured = cur_featured.filter(pl.col("snapshot_timestamp").dt.date() == target_date)
    if cur_featured.is_empty():
        logger.info(
            "No featured rows for %s after warmup load — skipping drift report", target_date
        )
        return _empty

    feature_cols = _feature_cols(cur_featured.to_pandas())
    cur_df = cur_featured.to_pandas()[feature_cols]

    # ------------------------------------------------------------------
    # 2. Load reference window (train days before model's saved_at)
    # ------------------------------------------------------------------
    try:
        meta = load_latest_metadata()
    except FileNotFoundError:
        logger.warning("No trained model found — skipping drift report")
        return _empty

    saved_at_str: str = meta.get("saved_at", "")
    try:
        saved_at = datetime.fromisoformat(saved_at_str)
    except (ValueError, TypeError):
        logger.warning(
            "Invalid saved_at in model metadata: %r — skipping drift report", saved_at_str
        )
        return _empty

    ref_end = saved_at.date()
    ref_start = (saved_at - timedelta(days=settings.train_days)).date()

    try:
        ref_polars = _load_bigquery_snapshots(ref_start, ref_end)
    except Exception as exc:
        logger.warning("Could not load reference snapshots (%s→%s): %s", ref_start, ref_end, exc)
        return _empty

    if ref_polars is None or ref_polars.is_empty():
        logger.warning("Empty reference window (%s→%s) — skipping drift report", ref_start, ref_end)
        return _empty

    # Sample by station before feature engineering so that lag/rolling features
    # are computed on complete time series per station (not broken random rows).
    # 100 stations × 7 days × 96 snapshots ≈ 67K rows — enough for drift detection.
    _DRIFT_STATION_SAMPLE = 100
    all_station_ids = ref_polars["station_id"].unique().to_list()
    if len(all_station_ids) > _DRIFT_STATION_SAMPLE:
        import random

        rng = random.Random(42)
        sampled_ids = rng.sample(all_station_ids, _DRIFT_STATION_SAMPLE)
        ref_polars = ref_polars.filter(pl.col("station_id").is_in(sampled_ids))
        logger.debug(
            "Reference data sampled to %d stations (%d rows) before feature engineering",
            _DRIFT_STATION_SAMPLE,
            len(ref_polars),
        )

    ref_featured = build_all_features(ref_polars)
    ref_df = ref_featured.to_pandas()[feature_cols]

    # ------------------------------------------------------------------
    # 3. Run Evidently drift report
    # ------------------------------------------------------------------
    try:
        from evidently import Report
        from evidently.presets import DataDriftPreset
    except ImportError as e:
        raise ImportError("Install evidently to generate drift reports.") from e

    report = Report([DataDriftPreset()])
    snapshot = report.run(reference_data=ref_df, current_data=cur_df)

    # ------------------------------------------------------------------
    # 4. Extract summary
    # ------------------------------------------------------------------
    # snapshot.dict() returns {"metrics": [...], "tests": [...]}
    # metrics[0] is DriftedColumnsCount with value {"count": N, "share": S}
    # remaining metrics are ValueDrift per column with the p-value as value
    metrics_list: list[dict[str, Any]] = snapshot.dict().get("metrics", [])

    drift_count_metric: dict[str, Any] = next(
        (m for m in metrics_list if "DriftedColumnsCount" in m.get("metric_name", "")), {}
    )
    n_drifted: int = int(drift_count_metric.get("value", {}).get("count", 0))
    share_drifted: float = float(drift_count_metric.get("value", {}).get("share", 0.0))

    drifted_names: list[str] = [
        m["config"]["column"]
        for m in metrics_list
        if "ValueDrift" in m.get("metric_name", "")
        and m.get("value", 1.0) < m.get("config", {}).get("threshold", 0.05)
    ]

    summary = {
        "date": target_date.isoformat(),
        "n_drifted_features": n_drifted,
        "share_drifted": share_drifted,
        "drifted_feature_names": drifted_names,
        "n_reference_rows": len(ref_df),
        "n_current_rows": len(cur_df),
    }

    logger.info(
        "Drift report for %s: %d/%d features drifted (%.1f%%)",
        target_date,
        n_drifted,
        len(feature_cols),
        share_drifted * 100,
    )

    # ------------------------------------------------------------------
    # 5. Save HTML + summary JSON to GCS
    # ------------------------------------------------------------------
    try:
        from google.cloud import storage as gcs

        gcs_client = gcs.Client(project=bq_project)
        bucket = gcs_client.bucket(gcs_bucket)

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp_html:
            html_path = Path(tmp_html.name)
        snapshot.save_html(str(html_path))

        html_blob_name = f"{_DRIFT_HTML_PREFIX}/{target_date}.html"
        bucket.blob(html_blob_name).upload_from_filename(str(html_path), content_type="text/html")
        logger.info("Drift report HTML uploaded to gs://%s/%s", gcs_bucket, html_blob_name)
        html_path.unlink(missing_ok=True)

        summary_blob_name = f"{_DRIFT_SUMMARY_PREFIX}/{target_date}_summary.json"
        bucket.blob(summary_blob_name).upload_from_string(
            json.dumps(summary, indent=2), content_type="application/json"
        )
        logger.info("Drift summary uploaded to gs://%s/%s", gcs_bucket, summary_blob_name)

    except Exception as exc:
        logger.warning("Could not upload drift report to GCS: %s", exc)

    return summary


# ---------------------------------------------------------------------------
# Entry point (called by the Airflow BashOperator)
# ---------------------------------------------------------------------------


def _yesterday_utc() -> date:
    return (datetime.now(UTC) - timedelta(days=1)).date()


if __name__ == "__main__":
    setup_logging()

    parser = argparse.ArgumentParser(description="Generate daily data drift report")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=_yesterday_utc(),
        help="Date to analyse (YYYY-MM-DD). Defaults to yesterday UTC.",
    )
    args = parser.parse_args()
    target: date = args.date

    logger.info("Generating drift report for %s", target)
    summary = generate_daily_drift_report(
        target,
        settings.gcp_project,
        settings.bq_dataset,
        settings.gcs_bucket,
    )
    logger.info("Drift report complete: %s", summary)
