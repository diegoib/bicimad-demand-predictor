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

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from src.common.logging_setup import setup_logging
from src.common.schemas import StationInfo
from src.mcp.server.data_layer import StationCatalog
from src.mcp.server.errors import StationNotFoundError

setup_logging(stream=sys.stderr)

mcp = MCPServer("bicimad")

_catalog = StationCatalog()


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


if __name__ == "__main__":
    mcp.run()
