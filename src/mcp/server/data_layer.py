"""Data layer for the BiciMAD MCP server — no MCP-specific code here.

Reads station status from BigQuery (`station_status_raw`), forecasts from
BigQuery (`predictions`, via src.serving.predictions_query), and lightweight
model metadata from MLflow. Testable with plain pytest; the MCP server
(server.py, added in a later phase) only wires these functions to
tools/resources/prompts and translates the exceptions in errors.py into
tool `is_error` results.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from src.common.config import settings
from src.common.schemas import BatchPredictionRow, StationInfo
from src.mcp.server.errors import ModelUnavailableError, StationNotFoundError
from src.serving.predictions_query import load_latest_prediction_for_station

logger = logging.getLogger(__name__)

_EARTH_RADIUS_KM = 6371.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometers."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _load_latest_station_snapshot() -> list[StationInfo]:
    """Query BigQuery for the latest station_status_raw partition.

    There is no dedicated station dimension table, so this reads the most
    recent row per active station directly from the raw ingestion table.

    Returns:
        One StationInfo per active station, from its most recent snapshot.

    Raises:
        ImportError: If google-cloud-bigquery is not installed.
    """
    try:
        from google.cloud import bigquery
    except ImportError as e:
        raise ImportError("Install google-cloud-bigquery to load the station catalog.") from e

    client = bigquery.Client(project=settings.gcp_project)
    query = f"""
        SELECT
            s.id AS station_id,
            s.name,
            s.number,
            s.activate,
            s.total_bases,
            s.dock_bikes,
            s.free_bases,
            s.geometry.coordinates[ORDINAL(2)] AS latitude,
            s.geometry.coordinates[ORDINAL(1)] AS longitude,
            s.ingestion_timestamp
        FROM `{settings.gcp_project}.{settings.bq_dataset}.station_status_raw` s
        WHERE s.activate = 1
        QUALIFY ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY s.ingestion_timestamp DESC) = 1
    """
    return [
        StationInfo(
            station_id=row["station_id"],
            name=row["name"],
            number=row["number"],
            activate=row["activate"],
            total_bases=row["total_bases"],
            dock_bikes=row["dock_bikes"],
            free_bases=row["free_bases"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            updated_at=row["ingestion_timestamp"],
        )
        for row in client.query(query)
    ]


class StationCatalog:
    """In-memory catalog of BiciMAD stations, refreshed lazily on a TTL.

    Reads the latest partition of `station_status_raw` (see
    `_load_latest_station_snapshot`). Refresh is lazy: a call made after the
    TTL expires re-queries BigQuery before answering — no background task.
    """

    def __init__(self, ttl_seconds: int | None = None) -> None:
        self._ttl_seconds = (
            ttl_seconds if ttl_seconds is not None else settings.mcp_cache_ttl_seconds
        )
        self._stations: dict[int, StationInfo] = {}
        self._loaded_at: float = 0.0

    def _ensure_fresh(self) -> None:
        age = time.monotonic() - self._loaded_at
        if self._stations and age < self._ttl_seconds:
            return
        stations = _load_latest_station_snapshot()
        self._stations = {s.station_id: s for s in stations}
        self._loaded_at = time.monotonic()
        logger.info("StationCatalog refreshed: %d stations", len(self._stations))

    def all(self) -> list[StationInfo]:
        """Return every station currently in the catalog."""
        self._ensure_fresh()
        return list(self._stations.values())

    def get(self, station_id: int) -> StationInfo:
        """Return a single station by id.

        Raises:
            StationNotFoundError: If no station has that id.
        """
        self._ensure_fresh()
        try:
            return self._stations[station_id]
        except KeyError:
            raise StationNotFoundError(f"No existe la estación {station_id}") from None

    def search(self, query: str) -> list[StationInfo]:
        """Return stations whose name contains `query` (case-insensitive)."""
        self._ensure_fresh()
        needle = query.strip().lower()
        return [s for s in self._stations.values() if needle in s.name.lower()]

    def find_nearest(
        self,
        lat: float,
        lon: float,
        k: int,
        min_bikes: int | None = None,
        min_docks: int | None = None,
    ) -> list[StationInfo]:
        """Return the k nearest stations to (lat, lon), closest first.

        Args:
            lat: Latitude of the reference point.
            lon: Longitude of the reference point.
            k: Number of stations to return.
            min_bikes: If set, only stations with at least this many dock_bikes.
            min_docks: If set, only stations with at least this many free_bases.

        Returns:
            Up to k StationInfo, sorted by distance ascending.
        """
        self._ensure_fresh()
        candidates = list(self._stations.values())
        if min_bikes is not None:
            candidates = [s for s in candidates if s.dock_bikes >= min_bikes]
        if min_docks is not None:
            candidates = [s for s in candidates if s.free_bases >= min_docks]

        candidates.sort(key=lambda s: _haversine_km(lat, lon, s.latitude, s.longitude))
        return candidates[:k]


def get_latest_forecast(station_id: int) -> BatchPredictionRow | None:
    """Return the most recent +1h forecast for a station, or None if absent."""
    return load_latest_prediction_for_station(station_id)


class _ModelMetadataCache:
    """Lazy TTL cache around `get_prod_model_metrics()`.

    Never downloads or deserializes the LightGBM booster — nothing in the
    MCP server runs inference, so only the lightweight MLflow metadata
    (mae, version, run_id) is needed.
    """

    def __init__(self, ttl_seconds: int | None = None) -> None:
        self._ttl_seconds = (
            ttl_seconds if ttl_seconds is not None else settings.mcp_cache_ttl_seconds
        )
        self._metadata: dict[str, Any] | None = None
        self._loaded_at: float = 0.0

    def get(self) -> dict[str, Any]:
        age = time.monotonic() - self._loaded_at
        if self._metadata is not None and age < self._ttl_seconds:
            return self._metadata

        from src.training.registry import get_prod_model_metrics

        metrics = get_prod_model_metrics()
        if metrics is None:
            raise ModelUnavailableError(
                f"No hay modelo con alias '@{settings.mlflow_prod_alias}' registrado en MLflow"
            )

        self._metadata = metrics
        self._loaded_at = time.monotonic()
        return self._metadata


_model_metadata_cache = _ModelMetadataCache()


def get_model_metadata() -> dict[str, Any]:
    """Return lightweight @prod model metadata from MLflow.

    Returns mae, version and run_id, cached in memory with the same TTL as
    StationCatalog and refreshed lazily.

    Raises:
        ModelUnavailableError: If no @prod alias is set in MLflow, or the
            MLflow tracking server is unreachable.
    """
    return _model_metadata_cache.get()
