.PHONY: setup ingest-local ingest-test features train serve test lint airflow-up airflow-down

PYTHON := python
SRC_DIR := src
TESTS_DIR := tests

setup:
	pip install -e ".[dev,ingestion,features,training,serving,monitoring]"
	pre-commit install

ingest-local:
	$(PYTHON) -m src.ingestion.main

ingest-test:
	BICIMAD_ENV=dev BICIMAD_MOCK=true $(PYTHON) -m src.ingestion.main

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

airflow-up:
	docker compose -f infra/docker-compose.yml up -d

airflow-down:
	docker compose -f infra/docker-compose.yml down
