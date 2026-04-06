"""Tests for src/features/feature_definitions.py."""

from src.common.schemas import FeatureRow
from src.features.feature_definitions import (
    ALL_FEATURES,
    FEATURE_NAMES,
    HISTORICAL_FEATURES,
    LAG_FEATURES,
    STATIC_FEATURES,
    TEMPORAL_FEATURES,
    WEATHER_FEATURES,
    get_features_by_group,
)

_VALID_GROUPS = {"lag", "temporal", "meteo", "stats", "static"}
_VALID_DTYPES = {"int", "float", "bool", "cat"}


class TestFeatureDefinitions:
    def test_all_features_count(self) -> None:
        assert len(ALL_FEATURES) == 35

    def test_group_counts(self) -> None:
        assert len(LAG_FEATURES) == 7
        assert len(TEMPORAL_FEATURES) == 7
        assert len(WEATHER_FEATURES) == 12
        assert len(HISTORICAL_FEATURES) == 5
        assert len(STATIC_FEATURES) == 4

    def test_feature_names_unique(self) -> None:
        assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))

    def test_all_names_match_feature_row(self) -> None:
        feature_row_fields = set(FeatureRow.model_fields.keys()) - {
            "station_id",
            "snapshot_timestamp",
            "target_dock_bikes_1h",
        }
        assert set(FEATURE_NAMES) == feature_row_fields

    def test_all_groups_valid(self) -> None:
        for f in ALL_FEATURES:
            assert f.group in _VALID_GROUPS, f"{f.name} has invalid group '{f.group}'"

    def test_all_dtypes_valid(self) -> None:
        for f in ALL_FEATURES:
            assert f.dtype in _VALID_DTYPES, f"{f.name} has invalid dtype '{f.dtype}'"

    def test_all_have_description(self) -> None:
        for f in ALL_FEATURES:
            assert f.description.strip(), f"{f.name} has empty description"

    def test_get_features_by_group_lag(self) -> None:
        lag = get_features_by_group("lag")
        assert len(lag) == 7
        assert all(f.group == "lag" for f in lag)

    def test_get_features_by_group_meteo(self) -> None:
        meteo = get_features_by_group("meteo")
        assert len(meteo) == 12

    def test_get_features_by_group_unknown(self) -> None:
        assert get_features_by_group("nonexistent") == []

    def test_feature_definitions_immutable(self) -> None:
        f = ALL_FEATURES[0]
        try:
            f.name = "modified"  # type: ignore[misc]
            assert False, "Should have raised an error"
        except Exception:
            pass  # frozen=True prevents mutation
