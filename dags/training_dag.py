"""Airflow DAG — BiciMAD daily model training.

Schedule: Every day at 03:00 UTC.
Launches a Cloud Run Job that runs the full training pipeline:
  1. Builds the training dataset from BigQuery (expanding window)
  2. Temporal split (train / val / test)
  3. Trains a LightGBM model (Optuna enabled)
  4. Saves artifacts to GCS under models/{version}/

After the Cloud Run Job completes, a local PythonOperator task
``register_and_promote`` runs on the Airflow VM to:
  5. Log all metrics and artifacts to the MLflow tracking server
  6. Compare the new model's MAE against the current @prod alias
  7. Promote to @prod only if the new model improves (or none exists yet)

Training runs in Cloud Run Jobs (not on the Airflow VM) to avoid
competing for RAM with the scheduler and webserver (e2-medium, 4 GB).

No business logic lives here — this DAG is pure orchestration.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator
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
    description="Daily BiciMAD LightGBM training + MLflow registration",
    schedule="0 3 * * *",  # Every day at 03:00 UTC
    start_date=datetime(2025, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["bicimad", "training"],
) as dag:
    # ------------------------------------------------------------------
    # Task 1: launch the Cloud Run Job that trains and saves to GCS.
    # ------------------------------------------------------------------
    train_task = CloudRunExecuteJobOperator(
        task_id="run_training_job",
        project_id=os.environ["BICIMAD_GCP_PROJECT"],
        region=os.environ.get("BICIMAD_GCP_REGION", "europe-west1"),
        job_name="bicimad-training",
        deferrable=False,  # poll synchronously — simpler on e2-medium
        overrides={
            "container_overrides": [
                {
                    "args": ["--end-date", "{{ macros.ds_add(ds, -1) }}"],
                }
            ]
        },
    )

    # ------------------------------------------------------------------
    # Task 2: register the freshly trained model in MLflow and promote
    # it to @prod if it improves over the current champion.
    # Runs on the Airflow VM so it can reach mlflow:5000 directly.
    # ------------------------------------------------------------------

    def _register_and_promote(**kwargs: object) -> None:
        from src.training.registry import (
            register_and_promote,  # lazy import — avoids loading lightgbm/matplotlib at DAG parse time
        )

        register_and_promote()

    register_task = PythonOperator(
        task_id="register_and_promote",
        python_callable=_register_and_promote,
    )

    train_task >> register_task
