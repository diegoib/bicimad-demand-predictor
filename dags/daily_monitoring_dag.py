"""Airflow DAG — BiciMAD daily monitoring job.

Runs at 06:05 UTC (5-minute offset from the ingestion DAG that fires at xx:00
to avoid resource contention on the e2-medium VM).

Three tasks run in parallel, then a final alert check aggregates results:

  1. ``compute_station_metrics`` — joins ``predictions`` × ``station_status_raw``
     for yesterday, writes per-station MAE/RMSE to ``station_daily_metrics``
     and overall aggregate to ``daily_totals``.

  2. ``generate_drift_report`` — compares yesterday's feature distributions
     against the 28-day training reference window using Evidently.  Uploads
     HTML report + JSON summary to GCS.

  3. ``run_alerts`` — reads the online MAE from ``cycle_metrics`` (last 24 h)
     and the drift summary from GCS; logs warnings if thresholds are exceeded.
     Depends on tasks 1 and 2.

No business logic lives here — this DAG is pure orchestration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

# ---------------------------------------------------------------------------
# Default arguments
# ---------------------------------------------------------------------------

default_args = {
    "owner": "bicimad",
    "depends_on_past": False,
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

_CMD_PREFIX = "cd /opt/airflow/project && PYTHONPATH=/opt/airflow/project"

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

with DAG(
    dag_id="bicimad_daily_monitoring",
    description="Daily per-station metrics, drift report, and performance alerts",
    schedule="5 6 * * *",
    start_date=datetime(2025, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["bicimad", "monitoring"],
) as dag:
    compute_station_metrics = BashOperator(
        task_id="compute_station_metrics",
        bash_command=f"{_CMD_PREFIX} python -m src.monitoring.daily_metrics",
    )

    generate_drift_report = BashOperator(
        task_id="generate_drift_report",
        bash_command=f"{_CMD_PREFIX} python -m src.monitoring.drift_report",
    )

    run_alerts = BashOperator(
        task_id="run_alerts",
        bash_command=f"{_CMD_PREFIX} python -m src.monitoring.alerts",
    )

    # Tasks run sequentially to avoid memory contention on the e2-medium VM (4 GB RAM).
    compute_station_metrics >> generate_drift_report >> run_alerts
