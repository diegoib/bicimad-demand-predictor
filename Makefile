.PHONY: setup features train serve test lint \
        airflow-up airflow-down airflow-vars \
        deploy-vm run-training-job

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

airflow-build:
	docker compose -f infra/docker-compose.yml build

airflow-up:
	docker compose -f infra/docker-compose.yml up -d --build

airflow-down:
	docker compose -f infra/docker-compose.yml down

## Set required Airflow Variables (run once on the VM after airflow-up).
## Usage: make airflow-vars GCP_PROJECT=my-project GCP_REGION=europe-west1
airflow-vars:
	docker compose -f infra/docker-compose.yml exec airflow-webserver \
		airflow variables set bicimad_gcp_project "$(GCP_PROJECT)"
	docker compose -f infra/docker-compose.yml exec airflow-webserver \
		airflow variables set bicimad_gcp_region "$(GCP_REGION)"

# ---------------------------------------------------------------------------
# GCP deployment
# ---------------------------------------------------------------------------

## Pull latest code on the Airflow VM and restart the scheduler.
## Usage: make deploy-vm VM_IP=1.2.3.4 VM_KEY=~/.ssh/bicimad_vm
deploy-vm:
	ssh -i "$(VM_KEY)" -o StrictHostKeyChecking=yes debian@$(VM_IP) \
		"cd ~/bicimad && git pull origin main && \
		 docker compose -f infra/docker-compose.yml restart airflow-scheduler"

## Trigger the Cloud Run training job manually.
## Usage: make run-training-job GCP_PROJECT=my-project GCP_REGION=europe-west1
run-training-job:
	gcloud run jobs execute bicimad-training \
		--region="$(GCP_REGION)" \
		--project="$(GCP_PROJECT)"
