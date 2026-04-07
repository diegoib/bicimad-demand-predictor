"""Tests for evaluate, naive_baseline, evaluate_critical_states, and generate_report."""

from __future__ import annotations

import json
import math
from pathlib import Path

import lightgbm as lgb
import polars as pl
import pytest
from sklearn.metrics import mean_absolute_error

from src.training.baseline import naive_baseline
from src.training.evaluate import evaluate, evaluate_critical_states, generate_report
from src.training.split import temporal_split
from src.training.train import train_model


def _train_on_large(large_featured_df: pl.DataFrame) -> tuple[lgb.Booster, pl.DataFrame]:
    train, val, test = temporal_split(large_featured_df, train_days=28, val_days=1, test_days=1)
    model = train_model(train, val, params={"n_estimators": 20, "num_leaves": 7})
    return model, test


class TestNaiveBaseline:
    def test_returns_mae_and_rmse(self, large_featured_df: pl.DataFrame) -> None:
        result = naive_baseline(
            large_featured_df.filter(pl.col("target_dock_bikes_1h").is_not_null())
        )
        assert "mae" in result
        assert "rmse" in result

    def test_mae_is_positive(self, large_featured_df: pl.DataFrame) -> None:
        result = naive_baseline(
            large_featured_df.filter(pl.col("target_dock_bikes_1h").is_not_null())
        )
        assert result["mae"] >= 0.0

    def test_rmse_gte_mae(self, large_featured_df: pl.DataFrame) -> None:
        result = naive_baseline(
            large_featured_df.filter(pl.col("target_dock_bikes_1h").is_not_null())
        )
        assert result["rmse"] >= result["mae"]

    def test_perfect_prediction_mae_is_zero(self) -> None:
        """If dock_bikes_now == target, MAE should be 0."""
        from datetime import UTC, datetime

        df = pl.DataFrame(
            {
                "dock_bikes_now": [10, 15, 20],
                "target_dock_bikes_1h": [10.0, 15.0, 20.0],
                "snapshot_timestamp": [datetime(2024, 1, 1, tzinfo=UTC)] * 3,
            }
        )
        result = naive_baseline(df)
        assert result["mae"] == pytest.approx(0.0)

    def test_raises_on_missing_column(self) -> None:
        df = pl.DataFrame({"dock_bikes_now": [10, 15]})
        with pytest.raises(ValueError, match="Missing columns"):
            naive_baseline(df)

    def test_mae_matches_sklearn(self, large_featured_df: pl.DataFrame) -> None:
        valid = large_featured_df.filter(pl.col("target_dock_bikes_1h").is_not_null())
        result = naive_baseline(valid)
        expected_mae = mean_absolute_error(
            valid["target_dock_bikes_1h"].to_numpy(),
            valid["dock_bikes_now"].to_numpy(),
        )
        assert result["mae"] == pytest.approx(expected_mae, rel=1e-6)


class TestEvaluate:
    def test_returns_expected_keys(self, large_featured_df: pl.DataFrame) -> None:
        model, test = _train_on_large(large_featured_df)
        metrics = evaluate(model, test)
        for key in (
            "mae",
            "rmse",
            "mae_normalized",
            "r2",
            "baseline_mae",
            "baseline_rmse",
            "improvement_pct",
            "n_rows",
        ):
            assert key in metrics, f"Missing key: {key}"

    def test_mae_is_non_negative(self, large_featured_df: pl.DataFrame) -> None:
        model, test = _train_on_large(large_featured_df)
        metrics = evaluate(model, test)
        assert metrics["mae"] >= 0.0

    def test_rmse_gte_mae(self, large_featured_df: pl.DataFrame) -> None:
        model, test = _train_on_large(large_featured_df)
        metrics = evaluate(model, test)
        assert metrics["rmse"] >= metrics["mae"] - 1e-9  # allow float rounding

    def test_improvement_pct_is_float(self, large_featured_df: pl.DataFrame) -> None:
        model, test = _train_on_large(large_featured_df)
        metrics = evaluate(model, test)
        assert isinstance(metrics["improvement_pct"], float)
        assert math.isfinite(metrics["improvement_pct"])

    def test_n_rows_matches_non_null_target(self, large_featured_df: pl.DataFrame) -> None:
        model, test = _train_on_large(large_featured_df)
        metrics = evaluate(model, test)
        expected_rows = test.filter(pl.col("target_dock_bikes_1h").is_not_null()).shape[0]
        assert int(metrics["n_rows"]) == expected_rows


class TestEvaluateCriticalStates:
    def test_returns_expected_keys(self, large_featured_df: pl.DataFrame) -> None:
        model, test = _train_on_large(large_featured_df)
        result = evaluate_critical_states(model, test)
        for key in ("empty_precision", "empty_recall", "full_precision", "full_recall"):
            assert key in result

    def test_precision_recall_in_range(self, large_featured_df: pl.DataFrame) -> None:
        model, test = _train_on_large(large_featured_df)
        result = evaluate_critical_states(model, test)
        for key in ("empty_precision", "empty_recall", "full_precision", "full_recall"):
            assert 0.0 <= result[key] <= 1.0


class TestGenerateReport:
    def test_writes_json_file(self, large_featured_df: pl.DataFrame, tmp_path: Path) -> None:
        model, test = _train_on_large(large_featured_df)
        metrics = evaluate(model, test)
        report_path = tmp_path / "report.json"
        generate_report(metrics, report_path, model=model)
        assert report_path.exists()

    def test_json_is_valid(self, large_featured_df: pl.DataFrame, tmp_path: Path) -> None:
        model, test = _train_on_large(large_featured_df)
        metrics = evaluate(model, test)
        report_path = tmp_path / "report.json"
        generate_report(metrics, report_path, model=model)
        with report_path.open() as f:
            data = json.load(f)
        assert "metrics" in data
        assert "generated_at" in data

    def test_report_contains_model_info(
        self, large_featured_df: pl.DataFrame, tmp_path: Path
    ) -> None:
        model, test = _train_on_large(large_featured_df)
        metrics = evaluate(model, test)
        report_path = tmp_path / "report.json"
        generate_report(metrics, report_path, model=model)
        with report_path.open() as f:
            data = json.load(f)
        assert "model_info" in data
        assert data["model_info"]["num_trees"] > 0

    def test_creates_parent_directories(
        self, large_featured_df: pl.DataFrame, tmp_path: Path
    ) -> None:
        model, test = _train_on_large(large_featured_df)
        metrics = evaluate(model, test)
        nested_path = tmp_path / "nested" / "dir" / "report.json"
        generate_report(metrics, nested_path, model=model)
        assert nested_path.exists()
