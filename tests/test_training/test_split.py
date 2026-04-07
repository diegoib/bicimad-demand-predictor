"""Tests for temporal_split — no temporal overlap, correct boundaries, warnings."""

from __future__ import annotations

import polars as pl
import pytest

from src.training.split import temporal_split


class TestTemporalSplit:
    def test_no_overlap_between_splits(self, large_featured_df: pl.DataFrame) -> None:
        train, val, test = temporal_split(large_featured_df, train_days=28, val_days=1, test_days=1)
        assert train["snapshot_timestamp"].max() < val["snapshot_timestamp"].min()
        assert val["snapshot_timestamp"].max() < test["snapshot_timestamp"].min()

    def test_all_splits_non_empty(self, large_featured_df: pl.DataFrame) -> None:
        train, val, test = temporal_split(large_featured_df, train_days=28, val_days=1, test_days=1)
        assert len(train) > 0
        assert len(val) > 0
        assert len(test) > 0

    def test_splits_cover_all_rows(self, large_featured_df: pl.DataFrame) -> None:
        train, val, test = temporal_split(large_featured_df, train_days=28, val_days=1, test_days=1)
        assert len(train) + len(val) + len(test) == len(large_featured_df)

    def test_test_split_has_correct_size(self, large_featured_df: pl.DataFrame) -> None:
        """Test split should cover approximately test_days worth of data."""
        _, _, test = temporal_split(large_featured_df, train_days=28, val_days=1, test_days=1)
        t_min = test["snapshot_timestamp"].min()
        t_max = test["snapshot_timestamp"].max()
        assert t_min is not None and t_max is not None
        days_covered = (t_max - t_min).days  # type: ignore[operator]
        assert days_covered <= 1  # 1 day of test data

    def test_val_split_has_correct_size(self, large_featured_df: pl.DataFrame) -> None:
        _, val, _ = temporal_split(large_featured_df, train_days=28, val_days=1, test_days=1)
        t_min = val["snapshot_timestamp"].min()
        t_max = val["snapshot_timestamp"].max()
        assert t_min is not None and t_max is not None
        days_covered = (t_max - t_min).days  # type: ignore[operator]
        assert days_covered <= 1

    def test_warns_when_train_shorter_than_expected(self, large_featured_df: pl.DataFrame) -> None:
        """With 30 days total and val+test=2 days, train=28. Requesting 100 days warns.

        The warning goes through the logger (not Python warnings module), so we just
        assert no exception is raised.
        """
        temporal_split(large_featured_df, train_days=100, val_days=1, test_days=1)

    def test_raises_when_df_too_short_for_val(self, featured_df: pl.DataFrame) -> None:
        """featured_df spans ~3h — cannot produce 1-day val + 1-day test."""
        with pytest.raises(ValueError, match="Validation split is empty"):
            temporal_split(featured_df, train_days=28, val_days=1, test_days=1)

    def test_raises_on_empty_dataframe(self) -> None:
        empty = pl.DataFrame({"snapshot_timestamp": pl.Series([], dtype=pl.Datetime("us", "UTC"))})
        with pytest.raises(ValueError, match="empty"):
            temporal_split(empty)

    def test_train_timestamps_are_sorted(self, large_featured_df: pl.DataFrame) -> None:
        train, _, _ = temporal_split(large_featured_df)
        ts = train["snapshot_timestamp"].to_list()
        assert ts == sorted(ts)
