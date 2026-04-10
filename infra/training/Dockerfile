FROM python:3.11-slim

WORKDIR /app

# Install only the dependencies needed for training
COPY pyproject.toml ./
COPY src/ src/

RUN pip install --no-cache-dir -e ".[training,ingestion]"

# Entrypoint: train.py reads start/end dates from env or CLI args,
# evaluates against the previous model in GCS, and registers if improved.
ENTRYPOINT ["python", "-m", "src.training.train"]
