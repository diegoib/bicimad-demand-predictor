"""Airflow DAG — BiciMAD station snapshot ingestion.

Runs every 15 minutes.  Calls ``src/ingestion/main.py`` via BashOperator to
spawn a clean subprocess (avoids fork() memory inheritance from the scheduler):
  1. Authenticates with the EMT API (token cached 23 h in prod via GCS or
     refreshed per-invocation when no persistent cache is available).
  2. Fetches all ~634 station snapshots.
  3. Fetches current weather from Open-Meteo.
  4. Writes the combined JSON to GCS (partitioned by date/hour/minute).
  5. Streams the flattened station rows into BigQuery.

No business logic lives here — this DAG is pure orchestration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

# ---------------------------------------------------------------------------
# Default arguments applied to every task
# ---------------------------------------------------------------------------

default_args = {
    "owner": "bicimad",
    "depends_on_past": False,
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=10),
}

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

with DAG(
    dag_id="bicimad_ingestion",
    description="Ingest BiciMAD station snapshots + weather every 15 minutes",
    schedule="*/15 * * * *",
    start_date=datetime(2025, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,  # avoid overlapping runs during API slowness
    default_args=default_args,
    tags=["bicimad", "ingestion"],
) as dag:
    # ------------------------------------------------------------------
    # Task: run one ingestion cycle
    # BashOperator spawns a fresh subprocess, avoiding the memory overhead
    # of fork()-ing the scheduler process (critical on memory-constrained VMs).
    # ------------------------------------------------------------------

    ingest_task = BashOperator(
        task_id="ingest_stations_and_weather",
        bash_command="cd /opt/airflow/project && PYTHONPATH=/opt/airflow/project python -m src.ingestion.main",
    )
