"""Tests for src/features/build_features.py.

Uses synthetic DataFrames from conftest.py with deterministic values to
verify correctness and absence of data leakage.
"""

from datetime import UTC

import polars as pl
import pytest

from src.features.build_features import (
    build_all_features,
    build_historical_features,
    build_lag_features,
    build_temporal_features,
    build_weather_features,
)


class TestBuildLagFeatures:
    def test_first_row_lag_15m_is_null(self, raw_df: pl.DataFrame) -> None:
        df = raw_df.sort(["station_id", "snapshot_timestamp"])
        result = build_lag_features(df)
        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        assert station1["dock_bikes_lag_15m"][0] is None

    def test_first_row_lag_1h_is_null(self, raw_df: pl.DataFrame) -> None:
        df = raw_df.sort(["station_id", "snapshot_timestamp"])
        result = build_lag_features(df)
        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        assert station1["dock_bikes_lag_1h"][0] is None

    def test_lag_15m_correct_at_row_1(self, raw_df: pl.DataFrame) -> None:
        # Station 1 dock_bikes[0] = 10 → lag_15m at row 1 should be 10.0
        df = raw_df.sort(["station_id", "snapshot_timestamp"])
        result = build_lag_features(df)
        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        assert station1["dock_bikes_lag_15m"][1] == pytest.approx(10.0)

    def test_lag_30m_correct_at_row_2(self, raw_df: pl.DataFrame) -> None:
        # Station 1 dock_bikes[0] = 10 → lag_30m at row 2 should be 10.0
        df = raw_df.sort(["station_id", "snapshot_timestamp"])
        result = build_lag_features(df)
        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        assert station1["dock_bikes_lag_30m"][2] == pytest.approx(10.0)

    def test_lag_1h_correct_at_row_4(self, raw_df: pl.DataFrame) -> None:
        # Station 1 dock_bikes[0] = 10 → lag_1h at row 4 should be 10.0
        df = raw_df.sort(["station_id", "snapshot_timestamp"])
        result = build_lag_features(df)
        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        assert station1["dock_bikes_lag_1h"][4] == pytest.approx(10.0)

    def test_no_cross_station_contamination(self, raw_df: pl.DataFrame) -> None:
        # Station 2 min dock_bikes = 20; all non-null lags must be >= 20
        df = raw_df.sort(["station_id", "snapshot_timestamp"])
        result = build_lag_features(df)
        station2 = result.filter(pl.col("station_id") == 2).sort("snapshot_timestamp")
        valid_lags = station2["dock_bikes_lag_15m"].drop_nulls()
        assert (valid_lags >= 20).all(), "Station 2 lag contains station 1 values (leakage)"

    def test_delta_dock_15m_correct(self, raw_df: pl.DataFrame) -> None:
        # At row 1: dock_bikes_now=11, lag_15m=10 → delta=1.0
        df = raw_df.sort(["station_id", "snapshot_timestamp"])
        result = build_lag_features(df)
        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        assert station1["delta_dock_15m"][1] == pytest.approx(1.0)

    def test_occupancy_rate_correct(self, raw_df: pl.DataFrame) -> None:
        # total_bases=24, dock_bikes=10 → occupancy=10/24
        df = raw_df.sort(["station_id", "snapshot_timestamp"])
        result = build_lag_features(df)
        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        assert station1["occupancy_rate_now"][0] == pytest.approx(10 / 24)

    def test_dock_bikes_now_equals_dock_bikes(self, raw_df: pl.DataFrame) -> None:
        df = raw_df.sort(["station_id", "snapshot_timestamp"])
        result = build_lag_features(df)
        assert (result["dock_bikes_now"] == result["dock_bikes"]).all()


class TestBuildTemporalFeatures:
    def _get_result(self, raw_df: pl.DataFrame) -> pl.DataFrame:
        df = raw_df.sort(["station_id", "snapshot_timestamp"])
        df = build_lag_features(df)
        return build_temporal_features(df)

    def test_hour_of_day(self, raw_df: pl.DataFrame) -> None:
        result = self._get_result(raw_df)
        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        # First slot is 09:00 UTC = 10:00 Madrid (CET)
        assert station1["hour_of_day"][0] == 10

    def test_day_of_week_monday(self, raw_df: pl.DataFrame) -> None:
        # 2024-01-15 is Monday → day_of_week = 0
        result = self._get_result(raw_df)
        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        assert station1["day_of_week"][0] == 0

    def test_is_weekend_false_monday(self, raw_df: pl.DataFrame) -> None:
        result = self._get_result(raw_df)
        station1 = result.filter(pl.col("station_id") == 1)
        assert not station1["is_weekend"].any()

    def test_month_january(self, raw_df: pl.DataFrame) -> None:
        result = self._get_result(raw_df)
        assert (result["month"] == 1).all()

    def test_is_holiday_false_jan15(self, raw_df: pl.DataFrame) -> None:
        # 2024-01-15 is not a holiday
        result = self._get_result(raw_df)
        assert not result["is_holiday"].any()

    def test_is_holiday_true_new_year(self) -> None:
        from datetime import datetime

        from src.features.build_features import build_lag_features, build_temporal_features

        row = {
            "station_id": 1,
            "station_number": "1",
            "station_name": "Test",
            "snapshot_timestamp": datetime(2024, 1, 1, 9, 0, tzinfo=UTC),
            "activate": 1,
            "no_available": 0,
            "total_bases": 24,
            "dock_bikes": 10,
            "free_bases": 14,
            "latitude": 40.42,
            "longitude": -3.70,
            "temperature_2m": 10.0,
            "apparent_temperature": 5.0,
            "precipitation": 0.0,
            "precipitation_probability": 0.0,
            "wind_speed_10m": 5.0,
            "weather_code": 1,
            "is_day": 1,
            "direct_radiation": 0.0,
        }
        df = pl.DataFrame([row]).with_columns(
            pl.col("snapshot_timestamp").cast(pl.Datetime("us", "UTC"))
        )
        df = build_lag_features(df)
        result = build_temporal_features(df)
        assert result["is_holiday"][0] is True

    def test_is_rush_hour_true_at_900(self, raw_df: pl.DataFrame) -> None:
        # 09:00 UTC = 10:00 Madrid → NOT rush hour (rush is 07:00-09:30 Madrid)
        # Actually 09:00 UTC = 10:00 CET — let's check minutes_since_midnight
        result = self._get_result(raw_df)
        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        # 10:00 Madrid = 600 minutes — not rush hour (rush ends at 9:30 = 570 min)
        assert station1["is_rush_hour"][0] is False

    def test_is_rush_hour_false_not_weekday(self) -> None:
        from datetime import datetime

        from src.features.build_features import build_lag_features, build_temporal_features

        # 2024-01-20 Saturday 08:00 UTC = 09:00 Madrid (within rush hour time window)
        row = {
            "station_id": 1,
            "station_number": "1",
            "station_name": "Test",
            "snapshot_timestamp": datetime(2024, 1, 20, 8, 0, tzinfo=UTC),
            "activate": 1,
            "no_available": 0,
            "total_bases": 24,
            "dock_bikes": 10,
            "free_bases": 14,
            "latitude": 40.42,
            "longitude": -3.70,
            "temperature_2m": 10.0,
            "apparent_temperature": 5.0,
            "precipitation": 0.0,
            "precipitation_probability": 0.0,
            "wind_speed_10m": 5.0,
            "weather_code": 1,
            "is_day": 1,
            "direct_radiation": 0.0,
        }
        df = pl.DataFrame([row]).with_columns(
            pl.col("snapshot_timestamp").cast(pl.Datetime("us", "UTC"))
        )
        df = build_lag_features(df)
        result = build_temporal_features(df)
        # Saturday → is_weekend=True → is_rush_hour must be False
        assert result["is_rush_hour"][0] is False

    def test_minutes_since_midnight_correct(self, raw_df: pl.DataFrame) -> None:
        # 09:00 UTC = 10:00 Madrid → 600 minutes since midnight
        result = self._get_result(raw_df)
        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        assert station1["minutes_since_midnight"][0] == 600


class TestBuildWeatherFeatures:
    def _get_result(self, raw_df: pl.DataFrame) -> pl.DataFrame:
        df = raw_df.sort(["station_id", "snapshot_timestamp"])
        df = build_lag_features(df)
        df = build_temporal_features(df)
        return build_weather_features(df)

    def test_precipitation_mm_exists(self, raw_df: pl.DataFrame) -> None:
        result = self._get_result(raw_df)
        assert "precipitation_mm" in result.columns

    def test_precipitation_mm_value(self, raw_df: pl.DataFrame) -> None:
        result = self._get_result(raw_df)
        # All rows have precipitation=1.5 from fixture
        assert all(v == pytest.approx(1.5) for v in result["precipitation_mm"].to_list())

    def test_is_raining_true(self, raw_df: pl.DataFrame) -> None:
        # precipitation=1.5 → is_raining=True
        result = self._get_result(raw_df)
        assert result["is_raining"].all()

    def test_feels_cold_true(self, raw_df: pl.DataFrame) -> None:
        # apparent_temperature=5.0 < 8.0 → feels_cold=True
        result = self._get_result(raw_df)
        assert result["feels_cold"].all()

    def test_feels_hot_false(self, raw_df: pl.DataFrame) -> None:
        # apparent_temperature=5.0 → feels_hot=False
        result = self._get_result(raw_df)
        assert not result["feels_hot"].any()

    def test_high_solar_radiation_true(self, raw_df: pl.DataFrame) -> None:
        # direct_radiation=500.0 > 400.0
        result = self._get_result(raw_df)
        assert result["high_solar_radiation"].all()

    def test_is_day_is_bool(self, raw_df: pl.DataFrame) -> None:
        result = self._get_result(raw_df)
        assert result["is_day"].dtype == pl.Boolean

    def test_is_raining_false_when_dry(self) -> None:
        from datetime import datetime

        from src.features.build_features import (
            build_lag_features,
            build_temporal_features,
            build_weather_features,
        )

        row = {
            "station_id": 1,
            "station_number": "1",
            "station_name": "Test",
            "snapshot_timestamp": datetime(2024, 1, 15, 9, 0, tzinfo=UTC),
            "activate": 1,
            "no_available": 0,
            "total_bases": 24,
            "dock_bikes": 10,
            "free_bases": 14,
            "latitude": 40.42,
            "longitude": -3.70,
            "temperature_2m": 10.0,
            "apparent_temperature": 35.0,  # hot
            "precipitation": 0.0,  # dry
            "precipitation_probability": 0.0,
            "wind_speed_10m": 5.0,
            "weather_code": 1,
            "is_day": 1,
            "direct_radiation": 100.0,
        }
        df = pl.DataFrame([row]).with_columns(
            pl.col("snapshot_timestamp").cast(pl.Datetime("us", "UTC"))
        )
        df = build_lag_features(df)
        df = build_temporal_features(df)
        result = build_weather_features(df)
        assert result["is_raining"][0] is False
        assert result["feels_hot"][0] is True
        assert result["feels_cold"][0] is False
        assert result["high_solar_radiation"][0] is False


class TestBuildHistoricalFeatures:
    def test_avg_dock_same_hour_7d_null_first_day(self, raw_df_history: pl.DataFrame) -> None:
        from src.features.build_features import (
            build_lag_features,
            build_temporal_features,
            build_weather_features,
        )

        df = raw_df_history.sort(["station_id", "snapshot_timestamp"])
        df = build_lag_features(df)
        df = build_temporal_features(df)
        df = build_weather_features(df)
        result = build_historical_features(df)

        from datetime import date

        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        # First day (2024-01-01) should have null avg_dock_same_hour_7d
        first_day = station1.filter(pl.col("snapshot_timestamp").dt.date() == date(2024, 1, 1))
        assert first_day["avg_dock_same_hour_7d"].is_null().all()

    def test_avg_dock_same_hour_7d_non_null_after_day2(self, raw_df_history: pl.DataFrame) -> None:
        from src.features.build_features import (
            build_lag_features,
            build_temporal_features,
            build_weather_features,
        )

        df = raw_df_history.sort(["station_id", "snapshot_timestamp"])
        df = build_lag_features(df)
        df = build_temporal_features(df)
        df = build_weather_features(df)
        result = build_historical_features(df)

        from datetime import date

        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        # Day 3 onward: avg should be non-null (has at least 1 prior day's data)
        after_day2 = station1.filter(pl.col("snapshot_timestamp").dt.date() >= date(2024, 1, 3))
        # At least some rows should have non-null values
        assert after_day2["avg_dock_same_hour_7d"].is_not_null().any()

    def test_avg_dock_same_hour_7d_value_correct(self, raw_df_history: pl.DataFrame) -> None:
        from src.features.build_features import (
            build_lag_features,
            build_temporal_features,
            build_weather_features,
        )

        # Station 1 dock_bikes is always 15 → 7-day rolling mean should be 15.0
        df = raw_df_history.sort(["station_id", "snapshot_timestamp"])
        df = build_lag_features(df)
        df = build_temporal_features(df)
        df = build_weather_features(df)
        result = build_historical_features(df)

        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        non_null = station1["avg_dock_same_hour_7d"].drop_nulls().to_list()
        assert all(v == pytest.approx(15.0) for v in non_null)

    def test_dock_bikes_same_time_1w_null_first_week(self, raw_df_history: pl.DataFrame) -> None:
        from src.features.build_features import (
            build_lag_features,
            build_temporal_features,
            build_weather_features,
        )

        df = raw_df_history.sort(["station_id", "snapshot_timestamp"])
        df = build_lag_features(df)
        df = build_temporal_features(df)
        df = build_weather_features(df)
        result = build_historical_features(df)

        from datetime import date

        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        first_week = station1.filter(pl.col("snapshot_timestamp").dt.date() < date(2024, 1, 8))
        assert first_week["dock_bikes_same_time_1w"].is_null().all()

    def test_dock_bikes_same_time_1w_non_null_after_week(
        self, raw_df_history: pl.DataFrame
    ) -> None:
        from src.features.build_features import (
            build_lag_features,
            build_temporal_features,
            build_weather_features,
        )

        df = raw_df_history.sort(["station_id", "snapshot_timestamp"])
        df = build_lag_features(df)
        df = build_temporal_features(df)
        df = build_weather_features(df)
        result = build_historical_features(df)

        from datetime import date

        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        after_week = station1.filter(pl.col("snapshot_timestamp").dt.date() >= date(2024, 1, 8))
        assert after_week["dock_bikes_same_time_1w"].is_not_null().any()


class TestTargetAlignment:
    def test_target_is_dock_bikes_60min_later(self, raw_df: pl.DataFrame) -> None:
        # At t=09:00 (slot 0), target should be dock_bikes at t=10:00 (slot 4)
        # Station 1: dock_bikes[4] = 14
        result = build_all_features(raw_df)
        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        from datetime import datetime

        t0 = datetime(2024, 1, 15, 9, 0, tzinfo=UTC)
        row_t0 = station1.filter(pl.col("snapshot_timestamp") == t0)
        assert len(row_t0) == 1
        assert row_t0["target_dock_bikes_1h"][0] == pytest.approx(14.0)

    def test_target_null_for_last_4_rows(self, raw_df: pl.DataFrame) -> None:
        result = build_all_features(raw_df)
        station1 = result.filter(pl.col("station_id") == 1).sort("snapshot_timestamp")
        last_4 = station1.tail(4)
        assert last_4["target_dock_bikes_1h"].is_null().all()

    def test_no_target_cross_station_leakage(self, raw_df: pl.DataFrame) -> None:
        # Station 1 dock_bikes max = 19; all non-null targets for station 1 must be <= 19
        result = build_all_features(raw_df)
        station1 = result.filter(pl.col("station_id") == 1)
        valid_targets = station1["target_dock_bikes_1h"].drop_nulls()
        assert (valid_targets <= 19).all()


class TestBuildAllFeatures:
    def test_output_sorted(self, raw_df: pl.DataFrame) -> None:
        result = build_all_features(raw_df)
        timestamps = result["snapshot_timestamp"].to_list()
        station_ids = result["station_id"].to_list()
        pairs = list(zip(station_ids, timestamps))
        assert pairs == sorted(pairs)

    def test_no_duplicate_rows(self, raw_df: pl.DataFrame) -> None:
        result = build_all_features(raw_df)
        unique = result.unique(subset=["station_id", "snapshot_timestamp"])
        assert len(unique) == len(result)

    def test_expected_columns_present(self, raw_df: pl.DataFrame) -> None:
        from src.features.feature_definitions import FEATURE_NAMES

        result = build_all_features(raw_df)
        for col in FEATURE_NAMES:
            assert col in result.columns, f"Missing feature column: {col}"
        assert "target_dock_bikes_1h" in result.columns

    def test_deduplication(self) -> None:
        from datetime import datetime

        # Create a DataFrame with a duplicate (station, timestamp)
        row = {
            "station_id": 1,
            "station_number": "1",
            "station_name": "Test",
            "snapshot_timestamp": datetime(2024, 1, 15, 9, 0, tzinfo=UTC),
            "activate": 1,
            "no_available": 0,
            "total_bases": 24,
            "dock_bikes": 10,
            "free_bases": 14,
            "latitude": 40.42,
            "longitude": -3.70,
            "temperature_2m": 10.0,
            "apparent_temperature": 5.0,
            "precipitation": 0.0,
            "precipitation_probability": 0.0,
            "wind_speed_10m": 5.0,
            "weather_code": 1,
            "is_day": 1,
            "direct_radiation": 0.0,
        }
        duplicate_row = dict(row)
        duplicate_row["dock_bikes"] = 99  # different value, same key

        df = pl.DataFrame([row, duplicate_row]).with_columns(
            pl.col("snapshot_timestamp").cast(pl.Datetime("us", "UTC"))
        )
        result = build_all_features(df)
        # After deduplication there should be exactly 1 row
        assert len(result) == 1
        # The last row (dock_bikes=99) is kept
        assert result["dock_bikes_now"][0] == 99
