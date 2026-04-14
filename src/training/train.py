"""LightGBM training pipeline for BiciMAD demand forecasting.

Provides two training modes:
- train_model: single run with default or provided hyperparameters.
- train_with_optuna: Bayesian hyperparameter search via Optuna.

Both functions accept Polars DataFrames and use val_df for early stopping only.
Final evaluation should be done on the held-out test split.

Usage:
    python -m src.training.train [--train-days 28]
                                 [--optuna] [--n-trials 50] [--output-dir PATH]
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import lightgbm as lgb
import pandas as pd
import polars as pl

from src.common.logging_setup import setup_logging
from src.features.feature_definitions import ALL_FEATURES, FEATURE_NAMES

logger = logging.getLogger(__name__)

# Features that LightGBM must treat as categorical
CATEGORICAL_FEATURES: list[str] = ["station_id", "distrito"]

# Boolean feature names — must be cast to Int8 before creating lgb.Dataset
_BOOL_FEATURE_NAMES: list[str] = [f.name for f in ALL_FEATURES if str(f.dtype) == "bool"]

# All columns passed to LightGBM (35 features + station_id)
ALL_FEATURE_COLS: list[str] = FEATURE_NAMES + ["station_id"]

# Default LightGBM hyperparameters
_DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "regression_l1",  # optimise MAE directly
    "n_estimators": 500,
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_child_samples": 20,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "n_jobs": -1,
    "verbose": -1,
    "random_state": 42,
}


def _prepare_features(
    df: pl.DataFrame,
    feature_cols: list[str] | None = None,
    target_col: str = "target_dock_bikes_1h",
) -> tuple[pd.DataFrame, pd.Series]:
    """Convert a featured Polars DataFrame to pandas X, y for LightGBM.

    - Drops rows where the target is null.
    - Casts boolean columns to Int8 (LightGBM rejects Python bool).
    - Casts categorical columns to pandas category dtype.

    Args:
        df: Featured Polars DataFrame.
        feature_cols: Columns to use as features. Defaults to ALL_FEATURE_COLS.
        target_col: Name of the target column.

    Returns:
        Tuple (X, y) as pandas DataFrame and Series.
    """
    cols = feature_cols if feature_cols is not None else ALL_FEATURE_COLS
    valid = df.filter(pl.col(target_col).is_not_null())

    # Cast booleans to Int8 so LightGBM can handle them
    bool_present = [c for c in _BOOL_FEATURE_NAMES if c in valid.columns and c in cols]
    if bool_present:
        valid = valid.with_columns([pl.col(c).cast(pl.Int8) for c in bool_present])

    available_cols = [c for c in cols if c in valid.columns]
    X = valid.select(available_cols).to_pandas()

    # Apply category dtype for categoricals
    for cat_col in CATEGORICAL_FEATURES:
        if cat_col in X.columns:
            X[cat_col] = X[cat_col].astype("category")

    y = valid[target_col].to_pandas()
    return X, y


def train_model(
    train_df: pl.DataFrame,
    val_df: pl.DataFrame,
    feature_cols: list[str] | None = None,
    target_col: str = "target_dock_bikes_1h",
    params: dict[str, Any] | None = None,
) -> lgb.Booster:
    """Train a LightGBM model with early stopping on val_df.

    Args:
        train_df: Training split (Polars DataFrame with all feature columns).
        val_df: Validation split used for early stopping only — not for final metrics.
        feature_cols: Feature columns to use. Defaults to ALL_FEATURE_COLS.
        target_col: Target column name.
        params: LightGBM hyperparameters. Defaults to _DEFAULT_PARAMS.

    Returns:
        Trained lgb.Booster.
    """
    effective_params = {**_DEFAULT_PARAMS, **(params or {})}

    X_train, y_train = _prepare_features(train_df, feature_cols, target_col)
    X_val, y_val = _prepare_features(val_df, feature_cols, target_col)

    if len(X_train) == 0:
        raise ValueError("Training set has no rows with non-null target.")
    if len(X_val) == 0:
        raise ValueError("Validation set has no rows with non-null target.")

    cat_features = [c for c in CATEGORICAL_FEATURES if c in X_train.columns]

    dtrain = lgb.Dataset(
        X_train, label=y_train, categorical_feature=cat_features, free_raw_data=False
    )
    dval = lgb.Dataset(
        X_val, label=y_val, categorical_feature=cat_features, free_raw_data=False, reference=dtrain
    )

    n_estimators = effective_params.pop("n_estimators", 500)

    callbacks = [
        lgb.early_stopping(stopping_rounds=50, verbose=False),
        lgb.log_evaluation(period=100),
    ]

    logger.info(
        "Training LightGBM — train: %d rows, val: %d rows, features: %d",
        len(X_train),
        len(X_val),
        len(X_train.columns),
    )

    booster = lgb.train(
        params=effective_params,
        train_set=dtrain,
        num_boost_round=n_estimators,
        valid_sets=[dval],
        callbacks=callbacks,  # type: ignore[arg-type]
    )

    logger.info(
        "Training complete — best iteration: %d, val MAE: %.4f",
        booster.best_iteration,
        booster.best_score.get("valid_0", {}).get("l1", float("nan")),
    )
    return booster


def train_with_optuna(
    train_df: pl.DataFrame,
    val_df: pl.DataFrame,
    n_trials: int = 50,
    feature_cols: list[str] | None = None,
    target_col: str = "target_dock_bikes_1h",
) -> tuple[dict[str, Any], lgb.Booster]:
    """Tune LightGBM hyperparameters with Optuna, then retrain with best params.

    Args:
        train_df: Training split.
        val_df: Validation split (used as Optuna objective).
        n_trials: Number of Optuna trials.
        feature_cols: Feature columns to use. Defaults to ALL_FEATURE_COLS.
        target_col: Target column name.

    Returns:
        Tuple (best_params, best_model).
    """
    import optuna  # noqa: PLC0415

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    X_train, y_train = _prepare_features(train_df, feature_cols, target_col)
    X_val, y_val = _prepare_features(val_df, feature_cols, target_col)
    cat_features = [c for c in CATEGORICAL_FEATURES if c in X_train.columns]

    dtrain = lgb.Dataset(
        X_train, label=y_train, categorical_feature=cat_features, free_raw_data=False
    )
    dval = lgb.Dataset(
        X_val, label=y_val, categorical_feature=cat_features, free_raw_data=False, reference=dtrain
    )

    def objective(trial: optuna.Trial) -> float:
        params: dict[str, Any] = {
            "objective": "regression_l1",
            "verbose": -1,
            "random_state": 42,
            "n_jobs": -1,
            "num_leaves": trial.suggest_int("num_leaves", 20, 300),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "min_child_samples": trial.suggest_int("min_child_samples", 10, 100),
            "subsample": trial.suggest_float("subsample", 0.5, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        }
        n_estimators = trial.suggest_int("n_estimators", 200, 1000)

        callbacks = [
            lgb.early_stopping(stopping_rounds=30, verbose=False),
            lgb.log_evaluation(period=0),
        ]
        booster = lgb.train(
            params=params,
            train_set=dtrain,
            num_boost_round=n_estimators,
            valid_sets=[dval],
            callbacks=callbacks,  # type: ignore[arg-type]
        )
        return float(booster.best_score["valid_0"]["l1"])

    logger.info("Starting Optuna search — %d trials", n_trials)
    study = optuna.create_study(direction="minimize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best_params = {
        **study.best_params,
        "objective": "regression_l1",
        "verbose": -1,
        "random_state": 42,
        "n_jobs": -1,
    }
    logger.info("Best params: %s  |  val MAE: %.4f", best_params, study.best_value)

    # Retrain with best params on full train set
    best_model = train_model(
        train_df, val_df, feature_cols=feature_cols, target_col=target_col, params=best_params
    )
    return best_params, best_model


if __name__ == "__main__":
    import argparse
    from datetime import timedelta

    setup_logging()

    parser = argparse.ArgumentParser(description="Train BiciMAD LightGBM model")
    parser.add_argument(
        "--end-date",
        default=date.today().isoformat(),
        help="Last date (inclusive) of the training window. Defaults to today. YYYY-MM-DD",
    )
    parser.add_argument(
        "--train-days",
        type=int,
        default=None,
        help="Training window size in days (default: BICIMAD_TRAIN_DAYS env var, fallback 28)",
    )
    parser.add_argument(
        "--output-dir", default=None, help="Model output directory (default: /tmp/models)"
    )
    parser.add_argument("--optuna", action="store_true", help="Use Optuna hyperparameter search")
    parser.add_argument("--n-trials", type=int, default=50)
    args = parser.parse_args()

    from src.common.config import settings
    from src.features.build_dataset import build_training_dataset
    from src.training.evaluate import evaluate, generate_report
    from src.training.registry import save_model
    from src.training.split import temporal_split

    train_days = args.train_days if args.train_days is not None else settings.train_days
    end = date.fromisoformat(args.end_date)
    start = end - timedelta(days=train_days + settings.val_days + settings.test_days)

    logger.info(
        "Training window: %s → %s (%d train + %d val + %d test days)",
        start,
        end,
        train_days,
        settings.val_days,
        settings.test_days,
    )

    df = build_training_dataset(start_date=start, end_date=end)
    train_df, val_df, test_df = temporal_split(df, train_days=train_days)

    if args.optuna:
        _, model = train_with_optuna(train_df, val_df, n_trials=args.n_trials)
    else:
        model = train_model(train_df, val_df)

    metrics = evaluate(model, test_df)
    print(
        f"Test MAE: {metrics['mae']:.4f}  vs baseline: {metrics['baseline_mae']:.4f}  ({metrics['improvement_pct']:.1f}% improvement)"
    )

    output_dir = Path(args.output_dir) if args.output_dir else None
    version_dir = save_model(model, metrics, output_dir=output_dir)
    print(f"Model saved to {version_dir}")

    report_path = version_dir / "evaluation_report.json"
    generate_report(metrics, report_path, model=model)
    print(f"Evaluation report saved to {report_path}")
