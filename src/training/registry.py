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

    Artifacts written to the version directory:
    - model.txt: LightGBM native text format.
    - metadata.json: metrics, feature names, top features by gain.
    - feature_importance.json: gain and split importance for all features.
    - feature_importance.png: bar chart of top 20 features by gain.

    Args:
        model: Trained LightGBM Booster.
        metrics: Evaluation metrics dict (from `evaluate`).
        output_dir: Local directory for model storage. Defaults to /tmp/models.

    Returns:
        Path to the version directory containing all artifacts.
    """
    from src.training.evaluate import (  # noqa: PLC0415
        compute_feature_importance,
        plot_feature_importance,
    )

    base_dir = Path(output_dir) if output_dir is not None else _DEFAULT_MODEL_DIR
    version = f"v{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    version_dir = base_dir / version
    version_dir.mkdir(parents=True, exist_ok=True)

    # Save LightGBM model in native text format
    model_path = version_dir / "model.txt"
    model.save_model(str(model_path))
    logger.info("Model saved to %s", model_path)

    # Compute feature importance
    importance = compute_feature_importance(model)
    top_features = [
        {"feature": r["feature"], "gain_pct": r["gain_pct"]} for r in importance["by_gain"][:5]
    ]

    # Save feature_importance.json
    fi_json_path = version_dir / "feature_importance.json"
    with fi_json_path.open("w") as f:
        json.dump(importance, f, indent=2)
    logger.info("Feature importance JSON saved to %s", fi_json_path)

    # Save feature_importance.png
    fi_png_path = version_dir / "feature_importance.png"
    plot_feature_importance(importance, fi_png_path)

    # Save metadata (includes top_features for quick inspection)
    metadata: dict[str, Any] = {
        "version": version,
        "saved_at": datetime.now(UTC).isoformat(),
        "feature_names": FEATURE_NAMES,
        "all_feature_cols": ALL_FEATURE_COLS,
        "num_features": len(ALL_FEATURE_COLS),
        "num_trees": model.num_trees(),
        "best_iteration": model.best_iteration,
        "top_features": top_features,
        "metrics": metrics,
    }
    metadata_path = version_dir / "metadata.json"
    with metadata_path.open("w") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Metadata saved to %s", metadata_path)

    _upload_to_gcs(version_dir, version)

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


def load_latest_metadata(
    model_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return metadata for the latest model version without loading the model itself.

    Downloads ``metadata.json`` from GCS if no local version exists.  Much
    lighter than ``load_latest_model`` — suitable for monitoring code that only
    needs metrics (e.g., ``alerts.py`` comparing online MAE vs training MAE).

    Args:
        model_dir: Directory containing version subdirectories. Defaults to /tmp/models.

    Returns:
        Parsed metadata dict (same shape as the second element of ``load_latest_model``).

    Raises:
        FileNotFoundError: If no versioned model directory is found locally or on GCS.
    """
    base_dir = Path(model_dir) if model_dir is not None else _DEFAULT_MODEL_DIR

    if not any(base_dir.glob("v*/")):
        logger.info("No local models found — downloading latest metadata from GCS...")
        _download_latest_from_gcs(base_dir, metadata_only=True)

    version_dirs = sorted(base_dir.glob("v*/"))
    if not version_dirs:
        raise FileNotFoundError(f"No versioned model found in {base_dir}")

    latest = version_dirs[-1]
    metadata_path = latest / "metadata.json"

    if not metadata_path.exists():
        raise FileNotFoundError(f"metadata.json not found in {latest}")

    with metadata_path.open() as f:
        metadata: dict[str, Any] = json.load(f)

    logger.info(
        "Loaded metadata for model %s — MAE: %.4f",
        latest.name,
        metadata.get("metrics", {}).get("mae", float("nan")),
    )
    return metadata


def _get_latest_gcs_version() -> str:
    """Return the version string of the most recently saved model in GCS.

    Returns:
        Version string, e.g. ``v20260419_030000``.

    Raises:
        FileNotFoundError: If no model versions exist in GCS.
    """
    try:
        import google.cloud.storage as storage
    except ImportError as e:
        raise ImportError("Install google-cloud-storage.") from e

    client = storage.Client(project=_settings.gcp_project)
    bucket = client.bucket(_settings.gcs_bucket)
    blobs = list(bucket.list_blobs(prefix="models/"))
    if not blobs:
        raise FileNotFoundError(f"No models found in gs://{_settings.gcs_bucket}/models/")
    versions: list[str] = sorted({b.name.split("/")[1] for b in blobs if b.name.count("/") >= 2})
    return versions[-1]


def _download_version_from_gcs(version: str, dest_dir: Path) -> None:
    """Download a specific model version from GCS to dest_dir.

    Args:
        version: Version string (e.g. ``v20260419_030000``).
        dest_dir: Local directory to write files into (created if needed).
    """
    try:
        import google.cloud.storage as storage
    except ImportError as e:
        raise ImportError("Install google-cloud-storage.") from e

    client = storage.Client(project=_settings.gcp_project)
    bucket = client.bucket(_settings.gcs_bucket)
    blobs = list(bucket.list_blobs(prefix=f"models/{version}/"))
    if not blobs:
        raise FileNotFoundError(
            f"No artifacts for version {version} in gs://{_settings.gcs_bucket}"
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    for blob in blobs:
        filename = blob.name.split("/")[-1]
        if not filename:
            continue
        local_path = dest_dir / filename
        blob.download_to_filename(str(local_path))
        logger.info("Downloaded %s from GCS", local_path)


def register_model_to_mlflow(version: str | None = None) -> tuple[str, float]:
    """Download a GCS model version, log all artifacts and metrics to MLflow,
    and register it in the Model Registry.

    Args:
        version: Version string (e.g. ``v20260419_030000``).
            If ``None``, the latest version in GCS is used.

    Returns:
        Tuple of ``(run_id, mae)`` for the newly created MLflow run.
    """
    import tempfile

    try:
        import mlflow
        import mlflow.lightgbm
    except ImportError as e:
        raise ImportError("Install mlflow to use the MLflow registry.") from e

    if version is None:
        version = _get_latest_gcs_version()

    mlflow.set_tracking_uri(_settings.mlflow_tracking_uri)
    mlflow.set_experiment(_settings.mlflow_experiment)

    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / version
        _download_version_from_gcs(version, dest)

        with (dest / "metadata.json").open() as f:
            metadata: dict[str, Any] = json.load(f)

        booster = lgb.Booster(model_file=str(dest / "model.txt"))

        with mlflow.start_run(run_name=version) as run:
            mlflow.log_params(
                {
                    "version": version,
                    "num_features": metadata.get("num_features"),
                    "num_trees": metadata.get("num_trees"),
                    "best_iteration": metadata.get("best_iteration"),
                }
            )

            raw_metrics = metadata.get("metrics", {})
            loggable = {k: float(v) for k, v in raw_metrics.items() if isinstance(v, int | float)}
            if loggable:
                mlflow.log_metrics(loggable)

            mlflow.lightgbm.log_model(
                booster,
                artifact_path="lgb_model",
                registered_model_name=_settings.mlflow_model_name,
            )

            # Log metadata.json as artifact so load_prod_model can retrieve it
            mlflow.log_artifact(str(dest / "metadata.json"))

            for fname in ("feature_importance.json", "feature_importance.png"):
                fp = dest / fname
                if fp.exists():
                    mlflow.log_artifact(str(fp))

            run_id: str = run.info.run_id

    mae = float(metadata.get("metrics", {}).get("mae", float("nan")))
    logger.info("Registered model version %s in MLflow (run_id=%s, MAE=%.4f)", version, run_id, mae)
    return run_id, mae


def get_prod_model_metrics() -> dict[str, Any] | None:
    """Return metrics for the current ``@prod`` alias, or ``None`` if unset.

    Returns:
        Dict with ``mae``, ``version``, ``run_id`` keys, or ``None`` if no
        ``@prod`` alias is set in the registry.
    """
    try:
        import mlflow
        from mlflow import MlflowClient
    except ImportError as e:
        raise ImportError("Install mlflow to use the MLflow registry.") from e

    mlflow.set_tracking_uri(_settings.mlflow_tracking_uri)
    client = MlflowClient()

    try:
        mv = client.get_model_version_by_alias(
            _settings.mlflow_model_name, _settings.mlflow_prod_alias
        )
    except Exception:
        return None

    if not mv.run_id:
        return None
    run = client.get_run(mv.run_id)
    mae = run.data.metrics.get("mae")
    if mae is None:
        return None

    return {"mae": float(mae), "version": mv.version, "run_id": mv.run_id}


def promote_to_prod(run_id: str) -> None:
    """Assign the ``@prod`` alias to the registered model version for this run.

    Args:
        run_id: MLflow run ID returned by ``register_model_to_mlflow``.

    Raises:
        ValueError: If no registered model version is found for the given run.
    """
    try:
        import mlflow
        from mlflow import MlflowClient
    except ImportError as e:
        raise ImportError("Install mlflow to use the MLflow registry.") from e

    mlflow.set_tracking_uri(_settings.mlflow_tracking_uri)
    client = MlflowClient()

    versions = sorted(
        client.search_model_versions(f"run_id='{run_id}'"),
        key=lambda v: int(v.version),
        reverse=True,
    )
    if not versions:
        raise ValueError(f"No registered model version found for run_id={run_id}")

    version_number = versions[0].version
    client.set_registered_model_alias(
        _settings.mlflow_model_name, _settings.mlflow_prod_alias, version_number
    )
    logger.info(
        "Set alias '@%s' on model '%s' version %s",
        _settings.mlflow_prod_alias,
        _settings.mlflow_model_name,
        version_number,
    )


def register_and_promote(version: str | None = None) -> None:
    """Register a GCS model version in MLflow and promote it to ``@prod`` if it improves.

    Intended to be called after the Cloud Run training job completes.
    If ``version`` is ``None``, the latest version in GCS is used automatically.

    Promotion rules:
    - No existing ``@prod`` alias → always promote (bootstrap).
    - Existing ``@prod`` → promote only if new MAE < current prod MAE.

    Args:
        version: GCS version string (e.g. ``v20260419_030000``).
            Defaults to the latest version in GCS.
    """
    run_id, new_mae = register_model_to_mlflow(version)

    prod = get_prod_model_metrics()
    if prod is None:
        logger.info(
            "No existing @%s model — auto-promoting %s (MAE=%.4f)",
            _settings.mlflow_prod_alias,
            run_id,
            new_mae,
        )
        promote_to_prod(run_id)
    elif new_mae < prod["mae"]:
        logger.info(
            "New model improves: MAE %.4f < prod MAE %.4f — promoting run %s",
            new_mae,
            prod["mae"],
            run_id,
        )
        promote_to_prod(run_id)
    else:
        logger.info(
            "New model does not improve: MAE %.4f >= prod MAE %.4f — keeping current @%s",
            new_mae,
            prod["mae"],
            _settings.mlflow_prod_alias,
        )


def load_prod_model(
    model_dir: str | Path | None = None,
) -> tuple[lgb.Booster, dict[str, Any]]:
    """Load the model tagged with ``@prod`` from MLflow.

    Falls back to ``load_latest_model`` if no ``@prod`` alias exists yet
    (bootstrap case: first training run before any alias is set).

    Args:
        model_dir: Passed to ``load_latest_model`` on fallback.

    Returns:
        Tuple of (lgb.Booster, metadata dict).
    """
    import tempfile

    try:
        import mlflow
        import mlflow.lightgbm
        from mlflow import MlflowClient
    except ImportError as e:
        raise ImportError("Install mlflow to use the MLflow registry.") from e

    mlflow.set_tracking_uri(_settings.mlflow_tracking_uri)
    client = MlflowClient()

    try:
        mv = client.get_model_version_by_alias(
            _settings.mlflow_model_name, _settings.mlflow_prod_alias
        )
    except Exception as exc:
        logger.warning(
            "No '@%s' model in MLflow (%s) — falling back to load_latest_model()",
            _settings.mlflow_prod_alias,
            exc,
        )
        return load_latest_model(model_dir)

    model_uri = f"models:/{_settings.mlflow_model_name}@{_settings.mlflow_prod_alias}"
    booster: lgb.Booster = mlflow.lightgbm.load_model(model_uri)

    with tempfile.TemporaryDirectory() as tmpdir:
        mlflow.artifacts.download_artifacts(
            run_id=mv.run_id,
            artifact_path="metadata.json",
            dst_path=tmpdir,
        )
        meta_path = Path(tmpdir) / "metadata.json"
        with meta_path.open() as f:
            metadata: dict[str, Any] = json.load(f)

    logger.info(
        "Loaded @%s model (version %s, run_id=%s) — MAE: %.4f",
        _settings.mlflow_prod_alias,
        mv.version,
        mv.run_id,
        metadata.get("metrics", {}).get("mae", float("nan")),
    )
    return booster, metadata


def _upload_to_gcs(version_dir: Path, version: str) -> None:
    """Upload all files in a version directory to GCS.

    Args:
        version_dir: Local directory containing model artifacts.
        version: Version string (e.g. ``v20260101_120000``) used as GCS prefix.
    """
    try:
        import google.cloud.storage as storage
    except ImportError as e:
        raise ImportError("Install google-cloud-storage to upload models to GCS.") from e

    client = storage.Client(project=_settings.gcp_project)
    bucket = client.bucket(_settings.gcs_bucket)

    for local_path in sorted(version_dir.iterdir()):
        if not local_path.is_file():
            continue
        blob_name = f"models/{version}/{local_path.name}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(local_path))
        logger.info("Uploaded %s to gs://%s/%s", local_path.name, _settings.gcs_bucket, blob_name)


def _download_latest_from_gcs(base_dir: Path, metadata_only: bool = False) -> None:
    """Download the latest model version from GCS to local disk.

    Args:
        base_dir: Local directory to download files into.
        metadata_only: If True, only download ``metadata.json`` (skip ``model.txt``).
    """
    try:
        import google.cloud.storage as storage
    except ImportError as e:
        raise ImportError("Install google-cloud-storage to download models from GCS.") from e

    client = storage.Client(project=_settings.gcp_project)
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
            filename = parts[2]
            if metadata_only and filename != "metadata.json":
                continue
            local_path = version_dir / filename
            blob.download_to_filename(str(local_path))
            logger.info("Downloaded %s from GCS", local_path)
