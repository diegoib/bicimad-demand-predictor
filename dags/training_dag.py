"""Airflow DAG — BiciMAD weekly model training.

Schedule: Sunday at 03:00 UTC.
Launches a Cloud Run Job that runs the full training pipeline:
  1. Builds the training dataset from BigQuery (expanding window)
  2. Temporal split (train / val / test)
  3. Trains a LightGBM model (Optuna enabled)
  4. Evaluates and compares with the currently active model
  5. Registers the new model to GCS only if it improves on the previous

Training runs in Cloud Run Jobs (not on the Airflow VM) to avoid
competing for RAM with the scheduler and webserver (e2-medium, 4 GB).

No business logic lives here — this DAG is pure orchestration.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from airflow import DAG
from airflow.providers.google.cloud.operators.cloud_run import CloudRunExecuteJobOperator

# ---------------------------------------------------------------------------
# Default arguments applied to every task
# ---------------------------------------------------------------------------

default_args = {
    "owner": "bicimad",
    "depends_on_past": False,
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
}

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

with DAG(
    dag_id="bicimad_training",
    description="Weekly BiciMAD LightGBM model training via Cloud Run Job",
    schedule="0 3 * * 0",  # Sunday at 03:00 UTC
    start_date=datetime(2025, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["bicimad", "training"],
) as dag:
    # ------------------------------------------------------------------
    # Task: launch the Cloud Run Job that runs the training pipeline.
    # The job image is defined in infra/training/Dockerfile and published
    # to Artifact Registry.  All training logic lives in src/training/.
    # ------------------------------------------------------------------

    train_task = CloudRunExecuteJobOperator(
        task_id="run_training_job",
        project_id=os.environ["BICIMAD_GCP_PROJECT"],
        region=os.environ.get("BICIMAD_GCP_REGION", "europe-west1"),
        job_name="bicimad-training",
        deferrable=False,  # poll synchronously — simpler on e2-medium
        # Pass end_date as the day before the DAG execution date: the execution
        # day itself has incomplete data, so the last full day is ds - 1.
        # start_date is computed inside train.py from end_date and the split constants.
        overrides={
            "container_overrides": [
                {
                    "args": ["--end-date", "{{ macros.ds_add(ds, -1) }}"],
                }
            ]
        },
    )
