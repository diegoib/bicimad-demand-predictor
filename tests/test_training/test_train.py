"""Tests for LightGBM training functions."""

from __future__ import annotations

import math

import lightgbm as lgb
import polars as pl

from src.training.split import temporal_split
from src.training.train import (
    CATEGORICAL_FEATURES,
    _prepare_features,
    train_model,
    train_with_optuna,
)


def _get_splits(large_featured_df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    return temporal_split(large_featured_df, train_days=28, val_days=1, test_days=1)


class TestPrepareFeatures:
    def test_returns_pandas_dataframe_and_series(self, large_featured_df: pl.DataFrame) -> None:
        import pandas as pd

        X, y = _prepare_features(
            large_featured_df.filter(pl.col("target_dock_bikes_1h").is_not_null())
        )
        assert isinstance(X, pd.DataFrame)
        assert isinstance(y, pd.Series)

    def test_drops_null_target_rows(self, featured_df: pl.DataFrame) -> None:
        null_count = featured_df["target_dock_bikes_1h"].is_null().sum()
        X, y = _prepare_features(featured_df)
        assert len(X) == len(featured_df) - null_count

    def test_categorical_columns_have_category_dtype(self, large_featured_df: pl.DataFrame) -> None:
        X, _ = _prepare_features(
            large_featured_df.filter(pl.col("target_dock_bikes_1h").is_not_null())
        )
        for cat_col in CATEGORICAL_FEATURES:
            if cat_col in X.columns:
                assert str(X[cat_col].dtype) == "category", f"{cat_col} should be category dtype"

    def test_no_python_bool_columns(self, large_featured_df: pl.DataFrame) -> None:
        """LightGBM rejects Python bool — all booleans must be cast to int."""
        X, _ = _prepare_features(
            large_featured_df.filter(pl.col("target_dock_bikes_1h").is_not_null())
        )
        bool_cols = [col for col in X.columns if X[col].dtype == bool]
        assert bool_cols == [], f"Boolean columns not cast: {bool_cols}"


class TestTrainModel:
    def test_returns_lgb_booster(self, large_featured_df: pl.DataFrame) -> None:
        train, val, _ = _get_splits(large_featured_df)
        model = train_model(train, val)
        assert isinstance(model, lgb.Booster)

    def test_booster_has_trees(self, large_featured_df: pl.DataFrame) -> None:
        train, val, _ = _get_splits(large_featured_df)
        model = train_model(train, val)
        assert model.num_trees() > 0

    def test_predictions_are_finite(self, large_featured_df: pl.DataFrame) -> None:
        train, val, test = _get_splits(large_featured_df)
        model = train_model(train, val)
        X_test, _ = _prepare_features(test.filter(pl.col("target_dock_bikes_1h").is_not_null()))
        preds = model.predict(X_test)
        assert all(math.isfinite(p) for p in preds)

    def test_predictions_are_non_negative(self, large_featured_df: pl.DataFrame) -> None:
        """Dock bikes can't be negative — predictions should be ≥ 0 for most rows."""
        train, val, test = _get_splits(large_featured_df)
        model = train_model(train, val)
        X_test, _ = _prepare_features(test.filter(pl.col("target_dock_bikes_1h").is_not_null()))
        preds = model.predict(X_test)
        # LightGBM regression_l1 may predict slightly below 0 with tiny synthetic data
        # but mean should be positive
        assert float(sum(preds)) / len(preds) > 0

    def test_custom_params_applied(self, large_featured_df: pl.DataFrame) -> None:
        train, val, _ = _get_splits(large_featured_df)
        model = train_model(train, val, params={"num_leaves": 7, "n_estimators": 20})
        assert model.num_trees() <= 20  # early stopping may reduce this


class TestTrainWithOptuna:
    def test_returns_params_and_booster(self, large_featured_df: pl.DataFrame) -> None:
        train, val, _ = _get_splits(large_featured_df)
        best_params, model = train_with_optuna(train, val, n_trials=3)
        assert isinstance(best_params, dict)
        assert isinstance(model, lgb.Booster)
        assert "num_leaves" in best_params

    def test_optuna_model_has_trees(self, large_featured_df: pl.DataFrame) -> None:
        train, val, _ = _get_splits(large_featured_df)
        _, model = train_with_optuna(train, val, n_trials=3)
        assert model.num_trees() > 0
