"""Open-Meteo weather client.

Fetches current-hour weather conditions for Madrid.
No API key required — Open-Meteo is free for non-commercial use.
"""

from datetime import UTC, datetime

import requests

from src.common.logging_setup import get_logger
from src.common.schemas import WeatherSnapshot

logger = get_logger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
MADRID_LAT = 40.4168
MADRID_LON = -3.7038

# All hourly variables fetched from the API
HOURLY_VARIABLES = [
    "temperature_2m",
    "apparent_temperature",
    "precipitation",
    "precipitation_probability",
    "wind_speed_10m",
    "weather_code",
    "is_day",
    "direct_radiation",
]


def fetch_current_weather(
    lat: float = MADRID_LAT,
    lon: float = MADRID_LON,
) -> WeatherSnapshot:
    """Fetch current weather conditions from Open-Meteo.

    Requests a 1-day hourly forecast and extracts the slot matching the
    current hour. Falls back to the last available slot if the current
    hour is not present in the response.

    Args:
        lat: Latitude of the location (default: Madrid).
        lon: Longitude of the location (default: Madrid).

    Returns:
        WeatherSnapshot for the current hour.

    Raises:
        requests.HTTPError: On non-2xx responses.
        KeyError: If the expected fields are missing in the API response.
    """
    params: dict[str, str | float | int] = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(HOURLY_VARIABLES),
        "timezone": "Europe/Madrid",
        "forecast_days": 1,
    }

    response = requests.get(OPEN_METEO_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    return _extract_current_hour(data)


def _extract_current_hour(data: dict) -> WeatherSnapshot:  # type: ignore[type-arg]
    """Extract the current-hour values from an Open-Meteo forecast response.

    Args:
        data: Raw JSON response dict from Open-Meteo.

    Returns:
        WeatherSnapshot for the current hour.
    """
    hourly = data["hourly"]
    times: list[str] = hourly["time"]  # "YYYY-MM-DDTHH:00"

    now_str = datetime.now().strftime("%Y-%m-%dT%H:00")
    if now_str in times:
        idx = times.index(now_str)
    else:
        idx = len(times) - 1
        logger.warning(
            "Current hour %s not found in Open-Meteo response, using last slot (%s)",
            now_str,
            times[idx],
        )

    # Open-Meteo returns precipitation_probability as int or None
    precip_prob = hourly["precipitation_probability"][idx]

    return WeatherSnapshot(
        timestamp=datetime.fromisoformat(times[idx]).replace(tzinfo=UTC),
        temperature_2m=float(hourly["temperature_2m"][idx]),
        apparent_temperature=float(hourly["apparent_temperature"][idx]),
        precipitation=float(hourly["precipitation"][idx]),
        precipitation_probability=float(precip_prob if precip_prob is not None else 0.0),
        wind_speed_10m=float(hourly["wind_speed_10m"][idx]),
        weather_code=int(hourly["weather_code"][idx]),
        is_day=int(hourly["is_day"][idx]),
        direct_radiation=float(hourly["direct_radiation"][idx]),
    )
