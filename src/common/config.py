"""Application configuration using Pydantic Settings.

All config is loaded from environment variables with the BICIMAD_ prefix.
Dev and prod both use GCP — dev uses a separate GCP project (e.g. bicimad-dev)
with Application Default Credentials (gcloud auth application-default login).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the BiciMAD demand predictor."""

    model_config = SettingsConfigDict(
        env_prefix="BICIMAD_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # GCP
    gcs_bucket: str = "bicimad-data"
    bq_dataset: str = "bicimad"
    bq_project: str = ""

    # Model
    model_version: str = "latest"


settings = Settings()
