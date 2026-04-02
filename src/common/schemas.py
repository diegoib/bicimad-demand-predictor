"""Pydantic v2 data schemas — single source of truth for all data contracts.

Used by: ingestion, features, training, serving.
When adding or modifying a feature, always update this file first.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# BiciMAD API schemas
# ---------------------------------------------------------------------------


class StationGeometry(BaseModel):
    """GeoJSON-style geometry for a station location."""

    type: str
    coordinates: list[float]  # [longitude, latitude]


class StationSnapshot(BaseModel):
    """Single station record from the BiciMAD API.

    Corresponds to one element in the `data` array of BicimadApiResponse.
    """

    model_config = {"extra": "ignore"}

    id: int
    number: str
    name: str
    activate: int = Field(description="1 = active, 0 = inactive")
    no_available: int = Field(description="1 = not available, 0 = available")
    total_bases: int = Field(description="Total number of docking slots")
    dock_bikes: int = Field(description="Currently docked bikes (available to rent)")
    free_bases: int = Field(description="Currently free bases (available to return)")
    geometry: StationGeometry


class BicimadApiResponse(BaseModel):
    """Top-level response from GET /v2/transport/bicimad/stations/."""

    model_config = {"extra": "ignore"}

    code: str = Field(description="'00' = success, '01'/'02' = error")
    description: str
    datetime: str
    data: list[StationSnapshot]


# ---------------------------------------------------------------------------
# Weather schemas
# ---------------------------------------------------------------------------


class WeatherSnapshot(BaseModel):
    """Current weather conditions from Open-Meteo."""

    timestamp: datetime
    temperature_2m: float = Field(description="Air temperature at 2m height, °C")
    apparent_temperature: float = Field(description="Feels-like temperature at 2m height, °C")
    precipitation: float = Field(description="Precipitation amount, mm")
    precipitation_probability: float = Field(description="Probability of precipitation, %")
    wind_speed_10m: float = Field(description="Wind speed at 10m height, km/h")
    weather_code: int = Field(description="WMO weather interpretation code")
    is_day: int = Field(description="1 = daytime, 0 = nighttime")
    direct_radiation: float = Field(description="Direct solar radiation at surface, W/m²")


# ---------------------------------------------------------------------------
# Feature / training schemas
# ---------------------------------------------------------------------------


class FeatureRow(BaseModel):
    """One row of the training / inference dataset.

    Contains all 29 features plus the target variable.
    Field descriptions map to feature_definitions.py groups.
    """

    # --- Identifiers ---
    station_id: int
    snapshot_timestamp: datetime

    # --- Lag features ---
    dock_bikes_now: int
    free_bases_now: int
    occupancy_rate_now: float = Field(description="dock_bikes / total_bases")
    dock_bikes_lag_15m: float | None = None
    dock_bikes_lag_30m: float | None = None
    dock_bikes_lag_1h: float | None = None
    delta_dock_15m: float | None = Field(
        default=None, description="dock_bikes_now - dock_bikes_lag_15m"
    )

    # --- Temporal features ---
    hour_of_day: int = Field(ge=0, le=23)
    day_of_week: int = Field(ge=0, le=6, description="0=Monday, 6=Sunday")
    is_weekend: bool
    month: int = Field(ge=1, le=12)
    is_holiday: bool
    minutes_since_midnight: int = Field(ge=0, le=1439)
    is_rush_hour: bool = Field(description="07:00-09:30 or 17:00-20:00 on weekdays")

    # --- Weather features ---
    temperature_2m: float
    apparent_temperature: float
    precipitation_mm: float
    precipitation_probability: float = Field(description="Probability of precipitation, 0-100 %")
    wind_speed_10m: float
    is_raining: bool
    weather_code: int
    is_day: bool
    direct_radiation: float = Field(description="Direct solar radiation at surface, W/m²")
    feels_cold: bool = Field(description="apparent_temperature < 8°C")
    feels_hot: bool = Field(description="apparent_temperature > 30°C")
    high_solar_radiation: bool = Field(description="direct_radiation > 400 W/m²")

    # --- Historical statistics ---
    avg_dock_same_hour_7d: float | None = None
    std_dock_same_hour_7d: float | None = None
    avg_dock_same_weekday: float | None = None
    station_daily_turnover: float | None = None
    dock_bikes_same_time_1w: float | None = None

    # --- Static station features ---
    total_bases: int
    latitude: float
    longitude: float
    distrito: str | None = None

    # --- Target (None during inference) ---
    target_dock_bikes_1h: float | None = Field(
        default=None,
        description="dock_bikes at t+60 minutes — training target",
    )


# ---------------------------------------------------------------------------
# Serving schemas
# ---------------------------------------------------------------------------


class PredictionOutput(BaseModel):
    """Response from the serving API."""

    station_id: int
    predicted_dock_bikes: float
    prediction_time: datetime = Field(description="Time for which the prediction applies (t+1h)")
    model_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)
