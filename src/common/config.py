"""Application configuration using Pydantic Settings.

All config is loaded from environment variables with the BICIMAD_ prefix.
Environment: dev (local DuckDB) or prod (GCP BigQuery).
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the BiciMAD demand predictor."""

    model_config = SettingsConfigDict(
        env_prefix="BICIMAD_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Environment
    env: Literal["dev", "prod"] = "dev"

    # GCP
    gcs_bucket: str = "bicimad-data"
    bq_dataset: str = "bicimad"
    bq_project: str = ""

    # EMT Madrid API credentials
    emt_email: str = ""
    emt_password: str = ""

    # Local paths (dev mode)
    local_data_dir: str = "data/raw"
    local_model_dir: str = "data/models"

    # Model
    model_version: str = "latest"

    # Feature flags
    mock: bool = False


settings = Settings()
