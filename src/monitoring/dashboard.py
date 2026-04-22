"""Generate a static HTML monitoring dashboard and upload it to GCS.

Reads from BigQuery (``cycle_metrics``, ``station_daily_metrics``) and from
GCS (latest drift summary JSON) to produce a single ``index.html`` file with
embedded Chart.js charts and HTML tables.  The file is uploaded to::

    gs://{bucket}/monitoring/dashboard/index.html

No server is required — access via a GCS signed URL or a public bucket.  The
full Streamlit dashboard is planned for Phase 8 (Cloud Run).

Usage::

    python -m src.monitoring.dashboard [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import textwrap
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.common.config import settings
from src.common.logging_setup import get_logger, setup_logging

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# BQ queries
# ---------------------------------------------------------------------------

_CYCLE_MAE_QUERY = """
    SELECT
        DATE(cycle_timestamp) AS day,
        AVG(mae)              AS avg_mae
    FROM `{project}.{dataset}.cycle_metrics`
    WHERE cycle_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
    GROUP BY day
    ORDER BY day
"""

_TOP10_STATIONS_QUERY = """
    SELECT
        station_id,
        model_version,
        daily_mae,
        daily_rmse,
        n_cycles
    FROM `{project}.{dataset}.station_daily_metrics`
    WHERE date = @target_date
    ORDER BY daily_mae DESC
    LIMIT 10
"""


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def _fetch_cycle_mae(bq_project: str, bq_dataset: str) -> list[dict[str, Any]]:
    from google.cloud import bigquery

    client = bigquery.Client(project=bq_project)
    query = _CYCLE_MAE_QUERY.format(project=bq_project, dataset=bq_dataset)
    return [
        {"day": str(row["day"]), "avg_mae": float(row["avg_mae"])} for row in client.query(query)
    ]


def _fetch_top10_stations(
    bq_project: str, bq_dataset: str, target_date: date
) -> list[dict[str, Any]]:
    from google.cloud import bigquery

    client = bigquery.Client(project=bq_project)
    query = _TOP10_STATIONS_QUERY.format(project=bq_project, dataset=bq_dataset)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("target_date", "DATE", target_date.isoformat())
        ]
    )
    return [
        {
            "station_id": int(row["station_id"]),
            "model_version": str(row["model_version"]),
            "daily_mae": float(row["daily_mae"]),
            "daily_rmse": float(row["daily_rmse"]),
            "n_cycles": int(row["n_cycles"]),
        }
        for row in client.query(query, job_config=job_config)
    ]


def _fetch_drift_summary(gcs_bucket: str, bq_project: str, target_date: date) -> dict[str, Any]:
    try:
        import google.cloud.storage as gcs

        client = gcs.Client(project=bq_project)
        bucket = client.bucket(gcs_bucket)
        blob = bucket.blob(f"monitoring/drift/{target_date}_summary.json")
        result: dict[str, Any] = json.loads(blob.download_as_text())
        return result
    except Exception as exc:
        logger.warning("Could not load drift summary for %s: %s", target_date, exc)
        return {}


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------


def _build_html(
    target_date: date,
    cycle_mae_rows: list[dict[str, Any]],
    top10: list[dict[str, Any]],
    drift_summary: dict[str, Any],
) -> str:
    labels = json.dumps([r["day"] for r in cycle_mae_rows])
    values = json.dumps([r["avg_mae"] for r in cycle_mae_rows])

    station_rows_html = (
        "\n".join(
            f"<tr><td>{r['station_id']}</td><td>{r['daily_mae']:.3f}</td>"
            f"<td>{r['daily_rmse']:.3f}</td><td>{r['n_cycles']}</td>"
            f"<td>{r['model_version']}</td></tr>"
            for r in top10
        )
        or "<tr><td colspan='5'>No data</td></tr>"
    )

    n_drifted = drift_summary.get("n_drifted_features", "—")
    share_pct = f"{drift_summary.get('share_drifted', 0.0) * 100:.1f}%" if drift_summary else "—"
    drifted_names = ", ".join(drift_summary.get("drifted_feature_names", [])) or "none"
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    return textwrap.dedent(
        f"""\
        <!DOCTYPE html>
        <html lang="es">
        <head>
          <meta charset="UTF-8">
          <title>BiciMAD Monitoring — {target_date}</title>
          <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
          <style>
            body {{ font-family: sans-serif; margin: 2rem; color: #333; }}
            h1 {{ color: #1a56db; }}
            h2 {{ margin-top: 2rem; border-bottom: 1px solid #ddd; padding-bottom: 0.3rem; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
            th, td {{ padding: 0.5rem 0.75rem; border: 1px solid #ccc; text-align: left; }}
            th {{ background: #f0f4ff; }}
            canvas {{ max-width: 800px; }}
            .meta {{ color: #888; font-size: 0.85rem; margin-top: 3rem; }}
          </style>
        </head>
        <body>
          <h1>BiciMAD — Monitoring Dashboard</h1>
          <p>Date: <strong>{target_date}</strong> &nbsp;|&nbsp; Generated: {generated_at}</p>

          <h2>MAE por ciclo — últimos 7 días</h2>
          <canvas id="maeChart"></canvas>

          <h2>Top 10 peores estaciones — {target_date}</h2>
          <table>
            <thead><tr>
              <th>Station ID</th><th>MAE diario</th><th>RMSE diario</th>
              <th>Ciclos</th><th>Modelo</th>
            </tr></thead>
            <tbody>{station_rows_html}</tbody>
          </table>

          <h2>Data drift — {target_date}</h2>
          <table>
            <thead><tr><th>Métrica</th><th>Valor</th></tr></thead>
            <tbody>
              <tr><td>Features con drift</td><td>{n_drifted}</td></tr>
              <tr><td>% features con drift</td><td>{share_pct}</td></tr>
              <tr><td>Features afectadas</td><td>{drifted_names}</td></tr>
            </tbody>
          </table>

          <p class="meta">Generado por src/monitoring/dashboard.py</p>

          <script>
            new Chart(document.getElementById("maeChart"), {{
              type: "line",
              data: {{
                labels: {labels},
                datasets: [{{
                  label: "MAE medio del ciclo",
                  data: {values},
                  borderColor: "#1a56db",
                  backgroundColor: "rgba(26,86,219,0.1)",
                  tension: 0.3,
                  fill: true,
                }}]
              }},
              options: {{
                scales: {{ y: {{ beginAtZero: false }} }},
                plugins: {{ legend: {{ display: false }} }}
              }}
            }});
          </script>
        </body>
        </html>
    """
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def generate_dashboard(
    target_date: date,
    bq_project: str,
    bq_dataset: str,
    gcs_bucket: str,
) -> None:
    """Build and upload the HTML dashboard for *target_date*.

    Args:
        target_date: The date whose station metrics and drift report to show.
        bq_project: GCP project ID.
        bq_dataset: BigQuery dataset name.
        gcs_bucket: GCS bucket name (without ``gs://``).
    """
    cycle_mae_rows = _fetch_cycle_mae(bq_project, bq_dataset)
    top10 = _fetch_top10_stations(bq_project, bq_dataset, target_date)
    drift_summary = _fetch_drift_summary(gcs_bucket, bq_project, target_date)

    html = _build_html(target_date, cycle_mae_rows, top10, drift_summary)

    from google.cloud import storage as gcs

    client = gcs.Client(project=bq_project)
    bucket = client.bucket(gcs_bucket)
    blob = bucket.blob("monitoring/dashboard/index.html")
    blob.upload_from_string(html, content_type="text/html")
    logger.info("Dashboard uploaded to gs://%s/monitoring/dashboard/index.html", gcs_bucket)


def _yesterday_utc() -> date:
    return (datetime.now(UTC) - timedelta(days=1)).date()


if __name__ == "__main__":
    setup_logging()

    parser = argparse.ArgumentParser(description="Generate static monitoring dashboard")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=_yesterday_utc(),
        help="Date to display (YYYY-MM-DD). Defaults to yesterday UTC.",
    )
    args = parser.parse_args()

    generate_dashboard(args.date, settings.gcp_project, settings.bq_dataset, settings.gcs_bucket)
