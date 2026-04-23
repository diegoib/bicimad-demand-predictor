# BiciMAD Demand Predictor

Batch ML system that forecasts bike availability (docked bikes) at every BiciMAD station in Madrid, 60 minutes ahead.

## Stack

| Layer | Technology |
|---|---|
| Model | LightGBM (global model across all stations) |
| Hyperparameter tuning | Optuna |
| Feature store / training data | BigQuery |
| Orchestration | Apache Airflow 2.x (self-hosted on a GCP e2-medium VM via Docker Compose) |
| Training compute | Google Cloud Run Jobs |
| Model registry | MLflow (self-hosted on a GCP VM) |
| Artifact storage | Google Cloud Storage |
| Serving | FastAPI reading pre-computed predictions from BigQuery |
| Monitoring | Evidently drift reports + custom alerting |
| Config / schemas | Pydantic v2, `pydantic-settings` |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│ Every 15 min (Airflow DAG: bicimad_ingestion)                       │
│   BiciMAD API ──► fetch stations                                    │
│   Open-Meteo API ──► fetch weather                                  │
│        │                                                            │
│        └──► combined raw payload ──► GCS                            │
│        └──► BigQuery station_status_raw                             │
│             (one row per station, weather_snapshot embedded         │
│                 in each row)                                        │
│        └──► batch inference ──► BigQuery predictions table          │
│             (using @prod model from MLflow)                         │
└─────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Daily at 03:00 UTC (Airflow DAG: bicimad_training)                  │
│   Cloud Run Job: build dataset ──► temporal split ──► LightGBM fit  │
│                  ──► evaluate ──► save artifacts to GCS             │
│   Airflow VM:    register in MLflow ──► promote to @prod if better  │
└─────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ FastAPI (make serve)                                                │
│   GET /predictions/latest      — latest batch for all stations      │
│   GET /predictions/{station_id} — latest prediction per station     │
│   GET /health                  — liveness + prediction count        │
└─────────────────────────────────────────────────────────────────────┘
```

## Features (35 total + station_id)

| Group | Count | Examples |
|---|---|---|
| Lag | 7 | `dock_bikes_now`, `dock_bikes_lag_15m/30m/1h`, `delta_dock_15m`, `occupancy_rate_now` |
| Temporal | 7 | `hour_of_day`, `day_of_week`, `is_weekend`, `is_holiday`, `is_rush_hour` |
| Weather | 12 | `temperature_2m`, `precipitation_mm`, `wind_speed_10m`, `is_raining`, `feels_cold/hot` |
| Historical stats | 5 | `avg_dock_same_hour_7d`, `avg_dock_same_weekday`, `dock_bikes_same_time_1w` |
| Static station | 4 | `total_bases`, `latitude`, `longitude`, `distrito` |

The same feature engineering function is used for both training and inference to avoid training–serving skew.

## Model training

- **Target**: `dock_bikes` at t+60 min per station.
- **Algorithm**: LightGBM regressor (`objective=regression_l1`, optimising MAE directly).
- **Split**: always temporal (train → val → test, no random shuffling).
- **Default window**: 7 days train + 1 day val + 1 day test + 7 days warm-up for rolling features.
- **Baseline**: naïve persistence — `dock_bikes(t+1h) = dock_bikes(t)`.
- **Hyperparameter search**: Optuna (Bayesian, `--optuna` flag or `--n-trials N`).
- **Versioning**: artifacts stored in GCS under `models/v{YYYYMMDD_HHMMSS}/` (model.txt, metadata.json, feature_importance.{json,png}, test_set.parquet).
- **Promotion**: a new model is tagged `@prod` in MLflow only if its MAE on the shared test set is lower than the current production model.

## Quick start

### Requirements

- Python 3.11+
- Docker (for Airflow and MLflow)
- GCP project with BigQuery, GCS, Cloud Run, and Secret Manager enabled

### Install

```bash
make setup
```

### GCP credentials

Both dev and prod environments use GCP. For local development, authenticate with Application Default Credentials against the `bicimad-dev` project:

```bash
gcloud auth application-default login
export BICIMAD_GCP_PROJECT=bicimad-dev
```

### EMT (BiciMAD API) credentials

Create a free account on the [EMT MobilityLabs portal](https://openapi.emtmadrid.es/). Store the credentials in Google Secret Manager under the secret names `bicimad-emt-email` and `bicimad-emt-password`. The ingestion client reads them automatically via ADC.

The API returns an `accessToken` valid for 24 hours. The client caches it in `/tmp/.bicimad_token_cache.json` (override with `BICIMAD_TOKEN_CACHE_PATH`) and renews it automatically.

### Environment variables

All variables use the `BICIMAD_` prefix. A `.env` file is also supported.

| Variable | Default | Description |
|---|---|---|
| `BICIMAD_GCP_PROJECT` | *(required)* | GCP project ID |
| `BICIMAD_GCS_BUCKET` | `bicimad-data` | GCS bucket for model artifacts |
| `BICIMAD_BQ_DATASET` | `bicimad` | BigQuery dataset name |
| `BICIMAD_MLFLOW_TRACKING_URI` | `http://mlflow:5000` | MLflow tracking server URL |
| `BICIMAD_MLFLOW_MODEL_NAME` | `bicimad-forecast` | Registered model name in MLflow |
| `BICIMAD_TRAIN_DAYS` | `7` | Training window size in days |
| `BICIMAD_VAL_DAYS` | `1` | Validation window in days |
| `BICIMAD_TEST_DAYS` | `1` | Test window in days |

## Common commands

```bash
make features            # Build the feature dataset from BigQuery
make train               # Train model locally
make train -- --optuna   # Train with Optuna hyperparameter search
make serve               # Start FastAPI server on :8000
make test                # Run the test suite
make lint                # ruff + mypy

make airflow-up          # Start Airflow (Docker Compose)
make airflow-down        # Stop Airflow

make mlflow-up           # Start MLflow tracking server (Docker Compose)
make mlflow-down         # Stop MLflow

# Deploy training image to Artifact Registry and run a manual training job
make deploy-training GCP_PROJECT=my-project GCP_REGION=europe-west1
make run-training-job    GCP_PROJECT=my-project GCP_REGION=europe-west1
```

## Repository structure

```
bicimad-demand-forecast/
├── src/
│   ├── common/         # Pydantic schemas, config, logging
│   ├── ingestion/      # BiciMAD and Open-Meteo API clients, GCS/BQ writers
│   ├── features/       # Feature engineering (build_features, feature_definitions)
│   ├── training/       # Train, evaluate, temporal split, model registry (MLflow + GCS)
│   ├── serving/        # FastAPI app — reads pre-computed predictions from BigQuery
│   └── monitoring/     # Drift reports, daily metrics, reconciliation, alerts
├── dags/               # Airflow DAGs (pure orchestration — no business logic)
│   ├── ingestion_dag.py
│   ├── training_dag.py
│   └── daily_monitoring_dag.py
├── tests/              # Unit tests mirroring src/ structure
├── infra/              # docker-compose.yml (Airflow), docker-compose.mlflow.yml, Terraform
├── docs/               # Design doc
├── notebooks/          # Exploratory analysis (not production)
├── pyproject.toml      # Unified dependency definitions
├── Makefile
├── PLAN.md             # Implementation checklist
└── CLAUDE.md           # AI assistant instructions for this repo
```

## Airflow DAGs

| DAG | Schedule | What it does |
|---|---|---|
| `bicimad_ingestion` | Every 15 min | Fetch station snapshots + weather → GCS → BQ → build features → write predictions |
| `bicimad_training` | Daily 03:00 UTC | Trigger Cloud Run training job → register in MLflow → promote to `@prod` if improved |
| `bicimad_daily_monitoring` | Daily | Aggregate per-station and overall daily error metrics, run drift report, fire alerts |

## Monitoring

- **Cycle reconciliation**: every 15 minutes, predictions made 1 hour ago are compared against the ground truth that has now arrived. MAE, RMSE, p50/p90 errors and worst-performing station are written to the `cycle_metrics` BigQuery table.
- **Daily metrics**: per-station and overall daily MAE/RMSE aggregated into `station_daily_metrics` and `daily_totals` tables.
- **Drift detection**: Evidently reports comparing recent feature distributions against the training baseline.
- **Alerts**: degradation alerts fire when online MAE exceeds a configurable threshold relative to the training MAE stored in model metadata.

## Data sources

- **BiciMAD API** — `GET https://openapi.emtmadrid.es/v2/transport/bicimad/stations/` — real-time station snapshots (requires EMT access token, sampled every 15 min).
- **Open-Meteo** — `GET https://api.open-meteo.com/v1/forecast` — hourly weather forecast for Madrid (lat 40.4168, lon -3.7038), no authentication required.
