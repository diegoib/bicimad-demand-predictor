"""Tests for src/mcp/server/data_layer.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from src.common.schemas import BatchPredictionRow, StationInfo
from src.mcp.server.data_layer import (
    StationCatalog,
    get_latest_forecast,
    get_model_metadata,
)
from src.mcp.server.errors import ModelUnavailableError, StationNotFoundError

# ---------------------------------------------------------------------------
# StationCatalog
# ---------------------------------------------------------------------------


def _catalog_with(stations: list[StationInfo]) -> StationCatalog:
    """Build a StationCatalog whose BigQuery load is mocked to `stations`."""
    catalog = StationCatalog(ttl_seconds=900)
    with patch(
        "src.mcp.server.data_layer._load_latest_station_snapshot",
        return_value=stations,
    ):
        catalog.all()  # force the first (mocked) load
    return catalog


class TestStationCatalogAll:
    def test_returns_every_station(self, sample_stations: list[StationInfo]) -> None:
        catalog = _catalog_with(sample_stations)
        assert len(catalog.all()) == len(sample_stations)


class TestStationCatalogGet:
    def test_returns_station_by_id(self, sample_stations: list[StationInfo]) -> None:
        catalog = _catalog_with(sample_stations)
        station = catalog.get(2)
        assert station.name == "2 - Atocha"

    def test_raises_for_unknown_id(self, sample_stations: list[StationInfo]) -> None:
        catalog = _catalog_with(sample_stations)
        with pytest.raises(StationNotFoundError):
            catalog.get(9999)


class TestStationCatalogSearch:
    def test_matches_partial_name_case_insensitive(
        self, sample_stations: list[StationInfo]
    ) -> None:
        catalog = _catalog_with(sample_stations)
        results = catalog.search("atocha")
        assert [s.station_id for s in results] == [2]

    def test_matches_multiple_stations(self, sample_stations: list[StationInfo]) -> None:
        catalog = _catalog_with(sample_stations)
        results = catalog.search("Puerta del Sol")
        assert {s.station_id for s in results} == {1, 3}

    def test_no_match_returns_empty(self, sample_stations: list[StationInfo]) -> None:
        catalog = _catalog_with(sample_stations)
        assert catalog.search("Nowhereland") == []


class TestStationCatalogFindNearest:
    _SOL_LAT, _SOL_LON = 40.4168, -3.7038

    def test_returns_k_closest_in_order(self, sample_stations: list[StationInfo]) -> None:
        catalog = _catalog_with(sample_stations)
        results = catalog.find_nearest(self._SOL_LAT, self._SOL_LON, k=3)
        assert len(results) == 3
        # Stations 1 and 3 sit right on Puerta del Sol; station 2 (Atocha) is
        # the next closest. Station 4 (Chamberí) and 5 (Vallecas) are farther.
        assert {results[0].station_id, results[1].station_id} == {1, 3}
        assert results[2].station_id == 2

    def test_filters_by_min_bikes(self, sample_stations: list[StationInfo]) -> None:
        catalog = _catalog_with(sample_stations)
        # Station 2 has 0 dock_bikes — must be excluded when min_bikes=1.
        results = catalog.find_nearest(self._SOL_LAT, self._SOL_LON, k=5, min_bikes=1)
        assert 2 not in [s.station_id for s in results]

    def test_filters_by_min_docks(self, sample_stations: list[StationInfo]) -> None:
        catalog = _catalog_with(sample_stations)
        # Station 4 has 0 free_bases — must be excluded when min_docks=1.
        results = catalog.find_nearest(self._SOL_LAT, self._SOL_LON, k=5, min_docks=1)
        assert 4 not in [s.station_id for s in results]


class TestStationCatalogTtl:
    def test_refreshes_lazily_after_ttl_expires(self, sample_stations: list[StationInfo]) -> None:
        catalog = StationCatalog(ttl_seconds=0)
        with patch(
            "src.mcp.server.data_layer._load_latest_station_snapshot",
            return_value=sample_stations,
        ) as mock_load:
            catalog.all()
            catalog.all()  # TTL is 0, so this should trigger a second load
        assert mock_load.call_count == 2

    def test_does_not_reload_within_ttl(self, sample_stations: list[StationInfo]) -> None:
        catalog = StationCatalog(ttl_seconds=900)
        with patch(
            "src.mcp.server.data_layer._load_latest_station_snapshot",
            return_value=sample_stations,
        ) as mock_load:
            catalog.all()
            catalog.all()
        assert mock_load.call_count == 1


# ---------------------------------------------------------------------------
# get_latest_forecast
# ---------------------------------------------------------------------------

_SNAP_TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class TestGetLatestForecast:
    def test_returns_prediction_when_present(self) -> None:
        row = BatchPredictionRow(
            station_id=1,
            prediction_made_at=_SNAP_TS,
            target_time=_SNAP_TS + timedelta(hours=1),
            predicted_dock_bikes=6.0,
            model_version="v20260101_120000",
        )
        with patch(
            "src.mcp.server.data_layer.load_latest_prediction_for_station",
            return_value=row,
        ):
            result = get_latest_forecast(1)
        assert result == row

    def test_returns_none_when_absent(self) -> None:
        with patch(
            "src.mcp.server.data_layer.load_latest_prediction_for_station",
            return_value=None,
        ):
            result = get_latest_forecast(9999)
        assert result is None


# ---------------------------------------------------------------------------
# get_model_metadata
# ---------------------------------------------------------------------------


class TestGetModelMetadata:
    def setup_method(self) -> None:
        # Reset the module-level singleton cache between tests.
        import src.mcp.server.data_layer as data_layer

        data_layer._model_metadata_cache = data_layer._ModelMetadataCache()

    def test_returns_metrics_from_registry(self) -> None:
        metrics = {"mae": 1.23, "version": "3", "run_id": "abc123"}
        with patch("src.training.registry.get_prod_model_metrics", return_value=metrics):
            result = get_model_metadata()
        assert result == metrics

    def test_raises_when_no_prod_alias(self) -> None:
        with patch("src.training.registry.get_prod_model_metrics", return_value=None):
            with pytest.raises(ModelUnavailableError):
                get_model_metadata()

    def test_caches_between_calls(self) -> None:
        metrics = {"mae": 1.23, "version": "3", "run_id": "abc123"}
        with patch(
            "src.training.registry.get_prod_model_metrics", return_value=metrics
        ) as mock_get:
            get_model_metadata()
            get_model_metadata()
        assert mock_get.call_count == 1
