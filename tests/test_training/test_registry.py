"""Tests for model registry — save and load."""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import pytest

from src.training.registry import load_latest_model, save_model
from src.training.split import temporal_split
from src.training.train import train_model


def _get_trained_model(large_featured_df: object) -> lgb.Booster:
    import polars as pl

    assert isinstance(large_featured_df, pl.DataFrame)
    train, val, _ = temporal_split(large_featured_df, train_days=28, val_days=1, test_days=1)
    return train_model(train, val, params={"n_estimators": 10, "num_leaves": 7})


class TestSaveModel:
    def test_creates_version_directory(self, large_featured_df: object, tmp_path: Path) -> None:
        model = _get_trained_model(large_featured_df)
        version_dir = save_model(model, {"mae": 1.5}, output_dir=tmp_path)
        assert version_dir.exists()
        assert version_dir.name.startswith("v")

    def test_saves_model_txt(self, large_featured_df: object, tmp_path: Path) -> None:
        model = _get_trained_model(large_featured_df)
        version_dir = save_model(model, {"mae": 1.5}, output_dir=tmp_path)
        assert (version_dir / "model.txt").exists()

    def test_saves_metadata_json(self, large_featured_df: object, tmp_path: Path) -> None:
        model = _get_trained_model(large_featured_df)
        version_dir = save_model(model, {"mae": 1.5, "rmse": 2.0}, output_dir=tmp_path)
        metadata_path = version_dir / "metadata.json"
        assert metadata_path.exists()
        with metadata_path.open() as f:
            meta = json.load(f)
        assert meta["metrics"]["mae"] == pytest.approx(1.5)
        assert "feature_names" in meta
        assert "version" in meta

    def test_returns_path_to_version_dir(self, large_featured_df: object, tmp_path: Path) -> None:
        model = _get_trained_model(large_featured_df)
        result = save_model(model, {}, output_dir=tmp_path)
        assert isinstance(result, Path)
        assert result.parent == tmp_path


class TestLoadLatestModel:
    def test_loads_saved_model(self, large_featured_df: object, tmp_path: Path) -> None:
        model = _get_trained_model(large_featured_df)
        save_model(model, {"mae": 1.5}, output_dir=tmp_path)
        loaded_model, metadata = load_latest_model(model_dir=tmp_path)
        assert isinstance(loaded_model, lgb.Booster)
        assert loaded_model.num_trees() == model.num_trees()

    def test_metadata_contains_metrics(self, large_featured_df: object, tmp_path: Path) -> None:
        model = _get_trained_model(large_featured_df)
        save_model(model, {"mae": 2.3}, output_dir=tmp_path)
        _, metadata = load_latest_model(model_dir=tmp_path)
        assert metadata["metrics"]["mae"] == pytest.approx(2.3)

    def test_loads_latest_when_multiple_versions(
        self, large_featured_df: object, tmp_path: Path
    ) -> None:
        import time

        model = _get_trained_model(large_featured_df)
        save_model(model, {"mae": 3.0}, output_dir=tmp_path)
        time.sleep(1)  # ensure different timestamp
        save_model(model, {"mae": 2.0}, output_dir=tmp_path)
        _, metadata = load_latest_model(model_dir=tmp_path)
        assert metadata["metrics"]["mae"] == pytest.approx(2.0)

    def test_raises_when_no_model_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_latest_model(model_dir=tmp_path)
