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
    gcp_project: str = ""
    gcp_region: str = "europe-west1"
    gcs_bucket: str = "bicimad-data"
    bq_dataset: str = "bicimad"

    # Model
    model_version: str = "latest"

    # Training split (days)
    train_days: int = 28
    val_days: int = 1
    test_days: int = 1
    # Extra historical days loaded from BQ before start_date to warm up
    # lag/rolling features. Equals the max rolling window in build_features.py
    # (7 days: avg_dock_same_hour_7d, station_daily_turnover, dock_bikes_same_time_1w).
    # Overridable via BICIMAD_FEATURE_WARMUP_DAYS.
    feature_warmup_days: int = 7


settings = Settings()
