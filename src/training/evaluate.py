"""Model evaluation functions for BiciMAD demand forecasting.

Provides MAE/RMSE metrics, critical-state precision/recall, and JSON report generation.
All functions accept a Polars DataFrame — the caller decides which split to pass (val or test).
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from src.features.feature_definitions import FEATURE_NAMES
from src.training.baseline import naive_baseline
from src.training.train import _prepare_features

logger = logging.getLogger(__name__)

# Boolean feature names that were cast to Int8 during training
_BOOL_FEATURES = {
    f.name
    for f in __import__("src.features.feature_definitions", fromlist=["ALL_FEATURES"]).ALL_FEATURES
    if str(f.dtype) == "bool"
}


def evaluate(model: lgb.Booster, df: pl.DataFrame) -> dict[str, float]:
    """Evaluate a trained LightGBM model on a DataFrame.

    Computes MAE, RMSE, normalised MAE, R², and improvement over the naive baseline.

    Args:
        model: Trained LightGBM Booster.
        df: Featured DataFrame with all feature columns and `target_dock_bikes_1h`.
            Rows with null target are dropped internally.

    Returns:
        Dictionary with keys: mae, rmse, mae_normalized, r2,
        baseline_mae, baseline_rmse, improvement_pct.
    """
    X, y = _prepare_features(df)
    if len(X) == 0:
        raise ValueError("No non-null target rows to evaluate.")

    preds = np.clip(np.round(model.predict(X)), 0, None)

    mae = float(mean_absolute_error(y, preds))
    rmse = math.sqrt(float(mean_squared_error(y, preds)))
    mae_norm = mae / float(y.mean()) if float(y.mean()) != 0 else float("nan")
    r2 = float(r2_score(y, preds))

    # Filter df to same rows used for evaluation (non-null target)
    eval_df = df.filter(pl.col("target_dock_bikes_1h").is_not_null())
    baseline = naive_baseline(eval_df)
    baseline_mae = baseline["mae"]
    improvement_pct = (baseline_mae - mae) / baseline_mae * 100 if baseline_mae != 0 else 0.0

    metrics: dict[str, float] = {
        "mae": mae,
        "rmse": rmse,
        "mae_normalized": mae_norm,
        "r2": r2,
        "baseline_mae": baseline_mae,
        "baseline_rmse": baseline["rmse"],
        "improvement_pct": improvement_pct,
        "n_rows": float(len(X)),
    }

    logger.info(
        "Evaluation — MAE: %.4f  RMSE: %.4f  R²: %.4f  vs baseline MAE: %.4f  (%.1f%% improvement)",
        mae,
        rmse,
        r2,
        baseline_mae,
        improvement_pct,
    )
    return metrics


def evaluate_critical_states(
    model: lgb.Booster,
    df: pl.DataFrame,
    empty_threshold: int = 2,
    full_threshold_offset: int = 2,
) -> dict[str, float]:
    """Evaluate model precision/recall for critical station states.

    Critical states:
    - Empty: target < empty_threshold (almost no bikes available)
    - Full: target > total_bases - full_threshold_offset (almost no free docks)

    Args:
        model: Trained LightGBM Booster.
        df: Featured DataFrame with `target_dock_bikes_1h` and `total_bases`.
        empty_threshold: Dock bikes below this count → station considered empty.
        full_threshold_offset: Station considered full when target > total_bases - offset.

    Returns:
        Dictionary with precision/recall for empty and full states.
    """
    X, y = _prepare_features(df)
    if len(X) == 0:
        return {}

    preds = np.clip(np.round(model.predict(X)), 0, None)
    eval_df = df.filter(pl.col("target_dock_bikes_1h").is_not_null())
    total_bases = eval_df["total_bases"].to_numpy()

    y_np = np.asarray(y.to_numpy(), dtype=np.float64)
    preds_np = np.asarray(preds, dtype=np.float64)

    # --- Empty state ---
    true_empty = y_np < empty_threshold
    pred_empty = preds_np < empty_threshold
    tp_empty = int(np.sum(true_empty & pred_empty))
    fp_empty = int(np.sum(~true_empty & pred_empty))
    fn_empty = int(np.sum(true_empty & ~pred_empty))
    precision_empty = tp_empty / (tp_empty + fp_empty) if (tp_empty + fp_empty) > 0 else 0.0
    recall_empty = tp_empty / (tp_empty + fn_empty) if (tp_empty + fn_empty) > 0 else 0.0

    # --- Full state ---
    full_threshold = total_bases - full_threshold_offset
    true_full = y_np > full_threshold
    pred_full = preds_np > full_threshold
    tp_full = int(np.sum(true_full & pred_full))
    fp_full = int(np.sum(~true_full & pred_full))
    fn_full = int(np.sum(true_full & ~pred_full))
    precision_full = tp_full / (tp_full + fp_full) if (tp_full + fp_full) > 0 else 0.0
    recall_full = tp_full / (tp_full + fn_full) if (tp_full + fn_full) > 0 else 0.0

    result: dict[str, float] = {
        "empty_precision": precision_empty,
        "empty_recall": recall_empty,
        "full_precision": precision_full,
        "full_recall": recall_full,
        "n_empty_actual": float(int(np.sum(true_empty))),
        "n_full_actual": float(int(np.sum(true_full))),
    }

    logger.info(
        "Critical states — Empty: precision=%.3f recall=%.3f | Full: precision=%.3f recall=%.3f",
        precision_empty,
        recall_empty,
        precision_full,
        recall_full,
    )
    return result


def compute_feature_importance(model: lgb.Booster) -> dict[str, list[dict[str, Any]]]:
    """Compute gain and split feature importance from a trained LightGBM model.

    Args:
        model: Trained LightGBM Booster.

    Returns:
        Dictionary with keys ``by_gain`` and ``by_split``, each a list of dicts
        sorted descending by importance. Each dict contains the feature name,
        raw importance value, and percentage of total.
    """
    names = model.feature_name()
    gains = model.feature_importance(importance_type="gain").tolist()
    splits = model.feature_importance(importance_type="split").tolist()

    total_gain = sum(gains) or 1.0
    total_split = sum(splits) or 1.0

    by_gain = sorted(
        [
            {"feature": n, "gain": round(g, 4), "gain_pct": round(g / total_gain * 100, 2)}
            for n, g in zip(names, gains)
        ],
        key=lambda x: x["gain"],
        reverse=True,
    )
    by_split = sorted(
        [
            {"feature": n, "split": s, "split_pct": round(s / total_split * 100, 2)}
            for n, s in zip(names, splits)
        ],
        key=lambda x: x["split"],
        reverse=True,
    )
    return {"by_gain": by_gain, "by_split": by_split}


def plot_feature_importance(
    importance_data: dict[str, list[dict[str, Any]]],
    output_path: str | Path,
    top_n: int = 20,
) -> None:
    """Save a horizontal bar chart of the top features by gain to disk.

    Args:
        importance_data: Output of ``compute_feature_importance``.
        output_path: Path for the output PNG file.
        top_n: Number of top features to plot (sorted by gain descending).
    """
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    top = importance_data["by_gain"][:top_n]
    # Reverse so highest-gain feature appears at the top of the chart
    features = [r["feature"] for r in reversed(top)]
    values = [r["gain_pct"] for r in reversed(top)]

    fig, ax = plt.subplots(figsize=(10, max(6, top_n * 0.4)))
    ax.barh(features, values, color="#4C72B0")
    ax.set_xlabel("Gain (%)")
    ax.set_title(f"Feature Importance — Top {len(top)} by Gain")
    ax.xaxis.grid(True, linestyle="--", alpha=0.5)
    ax.set_axisbelow(True)
    fig.tight_layout()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=120)
    plt.close(fig)

    logger.info("Feature importance plot saved to %s", output_path)


def generate_report(
    metrics: dict[str, Any],
    output_path: str | Path,
    model: lgb.Booster | None = None,
) -> None:
    """Write a JSON evaluation report to disk.

    Args:
        metrics: Dictionary of evaluation metrics (from `evaluate`).
        output_path: Path for the output JSON file.
        model: Optional Booster — if provided, best iteration and num_trees are included.
    """
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "feature_count": len(FEATURE_NAMES) + 1,  # +1 for station_id
        "metrics": metrics,
    }
    if model is not None:
        report["model_info"] = {
            "num_trees": model.num_trees(),
            "best_iteration": model.best_iteration,
        }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(report, f, indent=2)

    logger.info("Evaluation report saved to %s", output_path)
