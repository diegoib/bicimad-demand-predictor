"""Domain exceptions for the BiciMAD MCP server's data layer.

Kept separate from MCP protocol concerns. The data layer (data_layer.py)
raises these; the MCP server (server.py, added in a later phase) is what
translates them into tool `is_error` results — this module knows nothing
about MCP.
"""


class StationNotFoundError(Exception):
    """Raised when a requested station_id does not exist in the catalog."""


class ModelUnavailableError(Exception):
    """Raised when no @prod model metadata is available in MLflow."""


class ForecastUnavailableError(Exception):
    """Raised when no recent forecast exists for a station."""
