FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src/ src/

# Install only what serving needs:
# - fastapi + uvicorn: HTTP server
# - lightgbm: load lgb.Booster
# - polars + pandas: feature engineering
# - pydantic + pydantic-settings: schemas + config
# - holidays: temporal features (is_holiday)
# - google-cloud-storage: GCS model download in prod
# No optuna, no scikit-learn, no airflow.
RUN pip install --no-cache-dir \
    "fastapi>=0.104" \
    "uvicorn[standard]>=0.24" \
    "lightgbm>=4.1" \
    "polars>=0.19" \
    "pandas>=2.1" \
    "pydantic>=2.0" \
    "pydantic-settings>=2.0" \
    "holidays>=0.45" \
    "python-dateutil>=2.8" \
    "google-cloud-storage>=2.10"

EXPOSE 8000

CMD ["uvicorn", "src.serving.app:app", "--host", "0.0.0.0", "--port", "8000"]
