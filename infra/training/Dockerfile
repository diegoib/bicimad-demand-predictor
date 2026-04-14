FROM python:3.11-slim

WORKDIR /app

# libgomp1 is required by LightGBM (OpenMP runtime)
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
  libgomp1 \
  && rm -rf /var/lib/apt/lists/*

# Install only the dependencies needed for training
COPY pyproject.toml ./
COPY src/ src/

RUN pip install --no-cache-dir -e ".[training,features,ingestion]"

# Entrypoint: train.py reads start/end dates from env or CLI args,
# evaluates against the previous model in GCS, and registers if improved.
ENTRYPOINT ["python", "-m", "src.training.train"]
