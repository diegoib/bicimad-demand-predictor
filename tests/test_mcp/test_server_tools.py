"""Tests for src/mcp/server/server.py.

Most tests call the @mcp.tool()/@mcp.resource()/@mcp.prompt()-decorated
functions directly (the decorator returns the original callable, so this
works without going through the MCP transport) — fast, and pinpoints
whether a failure is in our logic or in the protocol plumbing. A couple of
tests go through a real in-process mcp.Client to confirm the protocol-level
behaviour (is_error, resource errors as MCPError) end to end.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent

import src.mcp.server.server as server_module
from mcp import Client
from src.common.schemas import BatchPredictionRow, StationInfo
from src.mcp.server.data_layer import StationCatalog

_SNAP_TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset_module_state() -> None:
    """Reset the module-level StationCatalog and model metadata caches.

    Both are singletons created once at import time in server.py, so
    without this, whichever test runs first would poison every later test
    with stale cached data.
    """
    server_module._catalog = StationCatalog()
    import src.mcp.server.data_layer as data_layer

    data_layer._model_metadata_cache = data_layer._ModelMetadataCache()


def _catalog_stations(stations: list[StationInfo]) -> AbstractContextManager[Any]:
    """Patch context manager loading the given stations into the module catalog."""
    return patch(
        "src.mcp.server.data_layer._load_latest_station_snapshot",
        return_value=stations,
    )


# ---------------------------------------------------------------------------
# Direct calls — tools
# ---------------------------------------------------------------------------


class TestGetStationStatusDirect:
    def test_returns_station(self, sample_stations: list[StationInfo]) -> None:
        with _catalog_stations(sample_stations):
            result = server_module.get_station_status(2)
        assert result.name == "2 - Atocha"

    def test_unknown_id_raises_tool_error(self, sample_stations: list[StationInfo]) -> None:
        with _catalog_stations(sample_stations), pytest.raises(ToolError, match="9999"):
            server_module.get_station_status(9999)


class TestSearchStationsDirect:
    def test_matches_by_name(self, sample_stations: list[StationInfo]) -> None:
        with _catalog_stations(sample_stations):
            result = server_module.search_stations("atocha")
        assert [s.station_id for s in result] == [2]


class TestFindNearestStationsDirect:
    def test_respects_k_and_filters(self, sample_stations: list[StationInfo]) -> None:
        with _catalog_stations(sample_stations):
            result = server_module.find_nearest_stations(40.4168, -3.7038, k=5, min_bikes=1)
        assert 2 not in [s.station_id for s in result]  # station 2 has 0 bikes


class TestForecastAvailabilityDirect:
    def test_returns_prediction(self) -> None:
        pred = BatchPredictionRow(
            station_id=1,
            prediction_made_at=_SNAP_TS,
            target_time=_SNAP_TS + timedelta(hours=1),
            predicted_dock_bikes=4.0,
            model_version="v1",
        )
        with patch(
            "src.mcp.server.data_layer.load_latest_prediction_for_station",
            return_value=pred,
        ):
            result = server_module.forecast_availability(1)
        assert result == pred

    def test_no_recent_prediction_raises_tool_error(self) -> None:
        with patch(
            "src.mcp.server.data_layer.load_latest_prediction_for_station",
            return_value=None,
        ):
            with pytest.raises(ToolError, match="No hay predicción reciente"):
                server_module.forecast_availability(1)


# ---------------------------------------------------------------------------
# Direct calls — resources
# ---------------------------------------------------------------------------


class TestStationsResourceDirect:
    def test_matches_catalog_count(self, sample_stations: list[StationInfo]) -> None:
        with _catalog_stations(sample_stations):
            result = server_module.stations_resource()
        assert len(result) == len(sample_stations)
        assert result[0]["station_id"] == sample_stations[0].station_id


class TestStationResourceDirect:
    def test_returns_station_dict(self, sample_stations: list[StationInfo]) -> None:
        with _catalog_stations(sample_stations):
            result = server_module.station_resource("2")
        assert result["name"] == "2 - Atocha"

    def test_unknown_id_raises_resource_not_found(self, sample_stations: list[StationInfo]) -> None:
        with _catalog_stations(sample_stations), pytest.raises(ResourceNotFoundError):
            server_module.station_resource("9999")

    def test_non_numeric_id_raises_resource_not_found(self) -> None:
        with pytest.raises(ResourceNotFoundError):
            server_module.station_resource("not-a-number")


class TestModelCardResourceDirect:
    def test_returns_metrics(self) -> None:
        metrics = {"mae": 1.5, "version": "3", "run_id": "abc"}
        with patch("src.training.registry.get_prod_model_metrics", return_value=metrics):
            result = server_module.model_card_resource()
        assert result == metrics

    def test_no_prod_alias_raises_resource_error(self) -> None:
        with patch("src.training.registry.get_prod_model_metrics", return_value=None):
            with pytest.raises(ResourceError):
                server_module.model_card_resource()


# ---------------------------------------------------------------------------
# Direct calls — prompts
# ---------------------------------------------------------------------------


class TestPromptsDirect:
    def test_plan_trip_includes_inputs(self) -> None:
        text = server_module.plan_trip("Sol", "Atocha", "18:00")
        assert "Sol" in text
        assert "Atocha" in text
        assert "18:00" in text

    def test_station_report_includes_station_id(self) -> None:
        text = server_module.station_report("42")
        assert "42" in text


# ---------------------------------------------------------------------------
# Integration — in-process Client (protocol-level behaviour)
# ---------------------------------------------------------------------------


@pytest.fixture()
def anyio_backend() -> str:
    """Restrict anyio's pytest plugin to the asyncio backend (no trio dependency)."""
    return "asyncio"


@pytest.mark.anyio()
class TestServerIntegration:
    async def test_lists_four_tools_three_resources_two_prompts(
        self, sample_stations: list[StationInfo]
    ) -> None:
        with _catalog_stations(sample_stations):
            async with Client(server_module.mcp) as client:
                tools = await client.list_tools()
                resources = await client.list_resources()
                templates = await client.list_resource_templates()
                prompts = await client.list_prompts()

        assert len(tools.tools) == 4
        assert all(t.description for t in tools.tools)
        assert len(resources.resources) == 2
        assert len(templates.resource_templates) == 1
        assert all(r.description for r in resources.resources)
        assert all(t.description for t in templates.resource_templates)
        assert len(prompts.prompts) == 2
        assert all(p.description for p in prompts.prompts)

    async def test_unknown_station_tool_call_is_error_not_crash(
        self, sample_stations: list[StationInfo]
    ) -> None:
        with _catalog_stations(sample_stations):
            async with Client(server_module.mcp) as client:
                result = await client.call_tool("get_station_status", {"station_id": 9999})

        assert result.is_error is True
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert "9999" in content.text

    async def test_unknown_station_resource_read_raises_mcp_error(
        self, sample_stations: list[StationInfo]
    ) -> None:
        with _catalog_stations(sample_stations):
            async with Client(server_module.mcp) as client:
                with pytest.raises(MCPError):
                    await client.read_resource("bicimad://stations/9999")
