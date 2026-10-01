"""BiciMAD MCP server — exposes station data over the Model Context Protocol.

Transport: stdio. All logging goes to stderr (stdout is the JSON-RPC
protocol channel in stdio transport and must never be mixed with log
output — see src.common.logging_setup.setup_logging).

Run with:
    uv run mcp dev src/mcp/server/server.py   # MCP Inspector
    uv run mcp run src/mcp/server/server.py   # plain stdio server
"""

from __future__ import annotations

import sys
from typing import Any

from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError

from mcp.server import MCPServer
from src.common.logging_setup import setup_logging
from src.common.schemas import BatchPredictionRow, StationInfo
from src.mcp.server.data_layer import StationCatalog, get_latest_forecast, get_model_metadata
from src.mcp.server.errors import (
    ForecastUnavailableError,
    ModelUnavailableError,
    StationNotFoundError,
)

setup_logging(stream=sys.stderr)

mcp = MCPServer("bicimad")

_catalog = StationCatalog()

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(name="get_station_status", description="Get the current status of a station.")
def get_station_status(station_id: int) -> StationInfo:
    """Return the current status of a BiciMAD station.

    This is the latest snapshot ingested for the station (available bikes,
    free docks, coordinates), not a forecast — for a +1h prediction use
    forecast_availability instead.

    Args:
        station_id: Numeric BiciMAD station ID.
    """
    try:
        return _catalog.get(station_id)
    except StationNotFoundError as e:
        raise ToolError(str(e)) from e


@mcp.tool(
    name="search_stations",
    description="Search BiciMAD stations by name (partial, case-insensitive match).",
)
def search_stations(query: str) -> list[StationInfo]:
    """Search stations by a partial, case-insensitive match on their name.

    Args:
        query: Text to search for in station names, e.g. "Atocha".
    """
    return _catalog.search(query)


@mcp.tool(
    name="find_nearest_stations",
    description="Find the k nearest BiciMAD stations to a coordinate, optionally filtered "
    "by minimum available bikes or docks.",
)
def find_nearest_stations(
    lat: float,
    lon: float,
    k: int,
    min_bikes: int | None = None,
    min_docks: int | None = None,
) -> list[StationInfo]:
    """Return the k nearest stations to a coordinate, closest first.

    Args:
        lat: Latitude of the reference point.
        lon: Longitude of the reference point.
        k: Number of stations to return.
        min_bikes: If set, only include stations with at least this many
            available bikes (dock_bikes).
        min_docks: If set, only include stations with at least this many
            free docks (free_bases).
    """
    return _catalog.find_nearest(lat, lon, k, min_bikes=min_bikes, min_docks=min_docks)


@mcp.tool(
    name="forecast_availability",
    description="Predicted dock_bikes availability at a fixed +1h horizon. "
    "No other horizon is supported.",
)
def forecast_availability(station_id: int) -> BatchPredictionRow:
    """Predicted bike availability for a station, +1 hour from the last forecast cycle.

    Fixed +1h horizon — this tool does not support any other horizon. Reads
    the latest pre-computed prediction from the batch pipeline (refreshed
    roughly every 15 minutes); it does not run the model on demand.

    Args:
        station_id: Numeric BiciMAD station ID.
    """
    try:
        forecast = get_latest_forecast(station_id)
        if forecast is None:
            raise ForecastUnavailableError(
                f"No hay predicción reciente para la estación {station_id}"
            )
        return forecast
    except ForecastUnavailableError as e:
        raise ToolError(str(e)) from e


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource(
    "bicimad://stations",
    name="stations",
    description="Full BiciMAD station catalog (latest snapshot).",
    mime_type="application/json",
)
def stations_resource() -> list[dict[str, Any]]:
    """Full station catalog, from the latest station_status_raw snapshot."""
    return [s.model_dump(mode="json") for s in _catalog.all()]


@mcp.resource(
    "bicimad://stations/{station_id}",
    name="station",
    description="A single BiciMAD station by id.",
    mime_type="application/json",
)
def station_resource(station_id: str) -> dict[str, Any]:
    """A single station's current status, by id.

    Args:
        station_id: Numeric BiciMAD station ID, as a URI path segment.
    """
    try:
        sid = int(station_id)
    except ValueError:
        raise ResourceNotFoundError(f"'{station_id}' no es un station_id numérico válido") from None

    try:
        return _catalog.get(sid).model_dump(mode="json")
    except StationNotFoundError as e:
        raise ResourceNotFoundError(str(e)) from e


@mcp.resource(
    "bicimad://model-card",
    name="model-card",
    description="Version and metrics of the @prod forecasting model registered in MLflow.",
    mime_type="application/json",
)
def model_card_resource() -> dict[str, Any]:
    """Version and metrics of the @prod model, read from MLflow metadata."""
    try:
        return get_model_metadata()
    except ModelUnavailableError as e:
        raise ResourceError(str(e)) from e


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@mcp.prompt(
    name="plan_trip",
    description="Plan a BiciMAD trip between two points in Madrid at a given time.",
)
def plan_trip(origen: str, destino: str, hora: str) -> str:
    """Draft a request to plan a BiciMAD trip using the available tools.

    Args:
        origen: Starting point, e.g. a street or landmark name.
        destino: Destination, e.g. a street or landmark name.
        hora: Planned departure time, e.g. "18:30" or "dentro de una hora".
    """
    return (
        f'Quiero ir en BiciMAD desde "{origen}" hasta "{destino}" alrededor de las {hora}. '
        "Busca las estaciones más cercanas al origen y al destino, comprueba su disponibilidad "
        "actual y, si la hora es dentro de más de una hora, usa la predicción a +1h. Dame una "
        "recomendación concreta de en qué estación coger la bici y en cuál dejarla."
    )


@mcp.prompt(
    name="station_report",
    description="Draft a status and forecast report for one BiciMAD station.",
)
def station_report(station_id: str) -> str:
    """Draft a request to build a status + forecast report for a station.

    Args:
        station_id: Numeric BiciMAD station ID.
    """
    return (
        f"Genera un informe breve de la estación BiciMAD con id {station_id}: su estado actual "
        "(bicis disponibles, anclajes libres) y su predicción de disponibilidad a +1 hora. "
        "Indica también si la estación está cerca de saturarse o de quedarse vacía."
    )


if __name__ == "__main__":
    mcp.run()
