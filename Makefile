.PHONY: setup features train serve test lint \
        airflow-up airflow-down \
        mlflow-up mlflow-down \
        run-training-job

PYTHON := python
SRC_DIR := src
TESTS_DIR := tests

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------

setup:
	pip install -e ".[dev,ingestion,features,training,serving,monitoring]"
	pre-commit install

features:
	$(PYTHON) -m src.features.build_dataset

train:
	$(PYTHON) -m src.training.train

serve:
	uvicorn src.serving.app:app --reload --host 0.0.0.0 --port 8000

test:
	pytest $(TESTS_DIR) -v --tb=short

lint:
	ruff check $(SRC_DIR) $(TESTS_DIR)
	mypy $(SRC_DIR)

# ---------------------------------------------------------------------------
# Airflow (local Docker Compose)
# ---------------------------------------------------------------------------

airflow-up:
	docker compose -f infra/docker-compose.yml up -d --build

airflow-down:
	docker compose -f infra/docker-compose.yml down

# ---------------------------------------------------------------------------
# MLflow (separate Docker Compose — runs on bicimad-mlflow VM)
# ---------------------------------------------------------------------------

mlflow-up:
	docker compose -f infra/docker-compose.mlflow.yml up -d --build

mlflow-down:
	docker compose -f infra/docker-compose.mlflow.yml down

# ---------------------------------------------------------------------------
# GCP deployment
# ---------------------------------------------------------------------------

## Trigger the Cloud Run training job manually.
## Usage: make run-training-job GCP_PROJECT=my-project GCP_REGION=europe-west1
run-training-job:
	gcloud run jobs execute bicimad-training \
		--region="$(GCP_REGION)" \
		--project="$(GCP_PROJECT)"
