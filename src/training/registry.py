"""Model versioning for BiciMAD demand forecasting.

Saves and loads LightGBM models with metadata (metrics, features, timestamp).
Always uploads to GCS after saving, and downloads from GCS when no local model exists.

Version format: v{YYYYMMDD_HHMMSS}
Directory layout:
    {model_dir}/
    └── v20260101_120000/
        ├── model.txt         (LightGBM native format)
        └── metadata.json     (metrics + version + feature_names + timestamp)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb

from src.common.config import settings as _settings
from src.features.feature_definitions import FEATURE_NAMES
from src.training.train import ALL_FEATURE_COLS

logger = logging.getLogger(__name__)


_DEFAULT_MODEL_DIR = Path("/tmp/models")


def save_model(
    model: lgb.Booster,
    metrics: dict[str, Any],
    output_dir: str | Path | None = None,
) -> Path:
    """Save a trained LightGBM model and its metadata to disk and GCS.

    Args:
        model: Trained LightGBM Booster.
        metrics: Evaluation metrics dict (from `evaluate`).
        output_dir: Local directory for model storage. Defaults to /tmp/models.

    Returns:
        Path to the version directory containing model.txt and metadata.json.
    """
    base_dir = Path(output_dir) if output_dir is not None else _DEFAULT_MODEL_DIR
    version = f"v{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    version_dir = base_dir / version
    version_dir.mkdir(parents=True, exist_ok=True)

    # Save LightGBM model in native text format
    model_path = version_dir / "model.txt"
    model.save_model(str(model_path))
    logger.info("Model saved to %s", model_path)

    # Save metadata
    metadata: dict[str, Any] = {
        "version": version,
        "saved_at": datetime.now(UTC).isoformat(),
        "feature_names": FEATURE_NAMES,
        "all_feature_cols": ALL_FEATURE_COLS,
        "num_features": len(ALL_FEATURE_COLS),
        "num_trees": model.num_trees(),
        "best_iteration": model.best_iteration,
        "metrics": metrics,
    }
    metadata_path = version_dir / "metadata.json"
    with metadata_path.open("w") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Metadata saved to %s", metadata_path)

    _upload_to_gcs(model_path, metadata_path, version)

    return version_dir


def load_latest_model(
    model_dir: str | Path | None = None,
) -> tuple[lgb.Booster, dict[str, Any]]:
    """Load the most recently saved model from disk, downloading from GCS if needed.

    Args:
        model_dir: Directory containing version subdirectories. Defaults to /tmp/models.

    Returns:
        Tuple of (lgb.Booster, metadata dict).

    Raises:
        FileNotFoundError: If no versioned model directory is found.
    """
    base_dir = Path(model_dir) if model_dir is not None else _DEFAULT_MODEL_DIR

    if not any(base_dir.glob("v*/")):
        logger.info("No local models found — downloading latest from GCS...")
        _download_latest_from_gcs(base_dir)

    version_dirs = sorted(base_dir.glob("v*/"))
    if not version_dirs:
        raise FileNotFoundError(f"No versioned model found in {base_dir}")

    latest = version_dirs[-1]
    model_path = latest / "model.txt"
    metadata_path = latest / "metadata.json"

    if not model_path.exists():
        raise FileNotFoundError(f"model.txt not found in {latest}")
    if not metadata_path.exists():
        raise FileNotFoundError(f"metadata.json not found in {latest}")

    booster = lgb.Booster(model_file=str(model_path))
    with metadata_path.open() as f:
        metadata = json.load(f)

    logger.info(
        "Loaded model %s — %d trees, MAE: %.4f",
        latest.name,
        booster.num_trees(),
        metadata.get("metrics", {}).get("mae", float("nan")),
    )
    return booster, metadata


def _upload_to_gcs(model_path: Path, metadata_path: Path, version: str) -> None:
    """Upload model files to GCS. Only called in prod mode."""
    try:
        from google.cloud import storage  # type: ignore[attr-defined]
    except ImportError as e:
        raise ImportError("Install google-cloud-storage to upload models to GCS.") from e

    client = storage.Client(project=_settings.bq_project)
    bucket = client.bucket(_settings.gcs_bucket)

    for local_path in (model_path, metadata_path):
        blob_name = f"models/{version}/{local_path.name}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(local_path))
        logger.info("Uploaded %s to gs://%s/%s", local_path.name, _settings.gcs_bucket, blob_name)


def _download_latest_from_gcs(base_dir: Path) -> None:
    """Download the latest model version from GCS to local disk."""
    try:
        from google.cloud import storage  # type: ignore[attr-defined]
    except ImportError as e:
        raise ImportError("Install google-cloud-storage to download models from GCS.") from e

    client = storage.Client(project=_settings.bq_project)
    bucket = client.bucket(_settings.gcs_bucket)

    blobs = list(bucket.list_blobs(prefix="models/"))
    if not blobs:
        raise FileNotFoundError(f"No models found in gs://{_settings.gcs_bucket}/models/")

    # Find latest version by name (v{YYYYMMDD_HHMMSS} sorts lexicographically)
    versions = sorted({b.name.split("/")[1] for b in blobs if b.name.count("/") >= 2})
    latest_version = versions[-1]

    version_dir = base_dir / latest_version
    version_dir.mkdir(parents=True, exist_ok=True)

    for blob in blobs:
        parts = blob.name.split("/")
        if len(parts) >= 3 and parts[1] == latest_version:
            local_path = version_dir / parts[2]
            blob.download_to_filename(str(local_path))
            logger.info("Downloaded %s from GCS", local_path)
