"""Tests for src/ingestion/weather_client.py."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.weather_client import _extract_current_hour, fetch_current_weather

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_open_meteo_response(current_hour_str: str) -> dict:  # type: ignore[type-arg]
    """Build a minimal Open-Meteo hourly response with 3 time slots."""
    prev_hour = current_hour_str[:11] + "00:00"  # not exact but enough for tests
    next_hour = current_hour_str[:11] + "02:00"

    times = [prev_hour, current_hour_str, next_hour]
    return {
        "hourly": {
            "time": times,
            "temperature_2m": [15.0, 22.5, 23.0],
            "apparent_temperature": [13.0, 20.1, 21.0],
            "precipitation": [0.0, 0.0, 0.2],
            "precipitation_probability": [5, 10, 30],
            "wind_speed_10m": [10.0, 15.3, 18.0],
            "weather_code": [0, 1, 3],
            "is_day": [0, 1, 1],
            "direct_radiation": [0.0, 350.0, 280.0],
        }
    }


# ---------------------------------------------------------------------------
# _extract_current_hour tests
# ---------------------------------------------------------------------------


class TestExtractCurrentHour:
    def test_extracts_matching_hour(self) -> None:
        now = datetime.now()
        current_str = now.strftime("%Y-%m-%dT%H:00")
        data = _build_open_meteo_response(current_str)

        snapshot = _extract_current_hour(data)

        assert snapshot.temperature_2m == 22.5
        assert snapshot.apparent_temperature == 20.1
        assert snapshot.precipitation == 0.0
        assert snapshot.precipitation_probability == 10.0
        assert snapshot.wind_speed_10m == 15.3
        assert snapshot.weather_code == 1
        assert snapshot.is_day == 1
        assert snapshot.direct_radiation == 350.0

    def test_fallback_to_last_slot_when_hour_not_found(self) -> None:
        # Use a past time that won't match now
        data = _build_open_meteo_response("2000-01-01T10:00")

        snapshot = _extract_current_hour(data)

        # Falls back to last slot (index 2)
        assert snapshot.temperature_2m == 23.0
        assert snapshot.weather_code == 3

    def test_timestamp_is_utc(self) -> None:
        now = datetime.now()
        current_str = now.strftime("%Y-%m-%dT%H:00")
        data = _build_open_meteo_response(current_str)

        snapshot = _extract_current_hour(data)

        assert snapshot.timestamp.tzinfo is UTC

    def test_none_precipitation_probability_defaults_to_zero(self) -> None:
        now = datetime.now()
        current_str = now.strftime("%Y-%m-%dT%H:00")
        data = _build_open_meteo_response(current_str)
        # Simulate Open-Meteo returning None for precipitation_probability
        data["hourly"]["precipitation_probability"][1] = None

        snapshot = _extract_current_hour(data)

        assert snapshot.precipitation_probability == 0.0


# ---------------------------------------------------------------------------
# fetch_current_weather tests
# ---------------------------------------------------------------------------


class TestFetchCurrentWeather:
    def test_fetch_current_weather_success(self) -> None:
        now = datetime.now()
        current_str = now.strftime("%Y-%m-%dT%H:00")
        mock_response = MagicMock()
        mock_response.json.return_value = _build_open_meteo_response(current_str)
        mock_response.raise_for_status.return_value = None

        with patch("src.ingestion.weather_client.requests.get", return_value=mock_response):
            snapshot = fetch_current_weather()

        assert snapshot.temperature_2m == 22.5
        assert snapshot.is_day == 1
        assert snapshot.direct_radiation == 350.0
        assert snapshot.precipitation_probability == 10.0

    def test_fetch_passes_correct_params(self) -> None:
        now = datetime.now()
        current_str = now.strftime("%Y-%m-%dT%H:00")
        mock_response = MagicMock()
        mock_response.json.return_value = _build_open_meteo_response(current_str)
        mock_response.raise_for_status.return_value = None

        with patch(
            "src.ingestion.weather_client.requests.get", return_value=mock_response
        ) as mock_get:
            fetch_current_weather(lat=40.4168, lon=-3.7038)

        params = mock_get.call_args.kwargs["params"]
        assert params["latitude"] == 40.4168
        assert params["longitude"] == -3.7038
        assert "apparent_temperature" in params["hourly"]
        assert "direct_radiation" in params["hourly"]
        assert "precipitation_probability" in params["hourly"]
        assert "is_day" in params["hourly"]

    def test_fetch_raises_on_http_error(self) -> None:
        import requests as req

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = req.HTTPError("503 Service Unavailable")

        with patch("src.ingestion.weather_client.requests.get", return_value=mock_response):
            with pytest.raises(req.HTTPError):
                fetch_current_weather()

    def test_weather_snapshot_has_all_required_fields(self) -> None:
        now = datetime.now()
        current_str = now.strftime("%Y-%m-%dT%H:00")
        mock_response = MagicMock()
        mock_response.json.return_value = _build_open_meteo_response(current_str)
        mock_response.raise_for_status.return_value = None

        with patch("src.ingestion.weather_client.requests.get", return_value=mock_response):
            snapshot = fetch_current_weather()

        # Verify all 8 WeatherSnapshot fields are populated
        assert snapshot.temperature_2m is not None
        assert snapshot.apparent_temperature is not None
        assert snapshot.precipitation is not None
        assert snapshot.precipitation_probability is not None
        assert snapshot.wind_speed_10m is not None
        assert snapshot.weather_code is not None
        assert snapshot.is_day is not None
        assert snapshot.direct_radiation is not None
