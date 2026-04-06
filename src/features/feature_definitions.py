"""Canonical feature catalog for the BiciMAD demand forecast model.

Each FeatureDefinition entry maps to a field in src/common/schemas.FeatureRow.
Features are organized in 5 groups:
  - lag:      Current station state and recent history
  - temporal: Time-based cyclical and categorical signals
  - meteo:    Weather conditions (direct and derived)
  - stats:    Historical statistical aggregations
  - static:   Station-level invariant attributes
"""

from pydantic import BaseModel, ConfigDict


class FeatureDefinition(BaseModel):
    """Metadata for a single model feature."""

    model_config = ConfigDict(frozen=True)

    name: str
    dtype: str  # "int" | "float" | "bool" | "cat"
    group: str  # "lag" | "temporal" | "meteo" | "stats" | "static"
    description: str


# ---------------------------------------------------------------------------
# Lag features (7)
# ---------------------------------------------------------------------------

LAG_FEATURES: list[FeatureDefinition] = [
    FeatureDefinition(
        name="dock_bikes_now",
        dtype="int",
        group="lag",
        description="Current number of docked bikes available to rent",
    ),
    FeatureDefinition(
        name="free_bases_now",
        dtype="int",
        group="lag",
        description="Current number of free bases available to return a bike",
    ),
    FeatureDefinition(
        name="occupancy_rate_now",
        dtype="float",
        group="lag",
        description="dock_bikes / total_bases — current occupancy ratio",
    ),
    FeatureDefinition(
        name="dock_bikes_lag_15m",
        dtype="float",
        group="lag",
        description="dock_bikes 15 minutes ago (nullable during warmup)",
    ),
    FeatureDefinition(
        name="dock_bikes_lag_30m",
        dtype="float",
        group="lag",
        description="dock_bikes 30 minutes ago (nullable during warmup)",
    ),
    FeatureDefinition(
        name="dock_bikes_lag_1h",
        dtype="float",
        group="lag",
        description="dock_bikes 60 minutes ago (nullable during warmup)",
    ),
    FeatureDefinition(
        name="delta_dock_15m",
        dtype="float",
        group="lag",
        description="dock_bikes_now minus dock_bikes_lag_15m — net change in 15 min",
    ),
]

# ---------------------------------------------------------------------------
# Temporal features (7)
# ---------------------------------------------------------------------------

TEMPORAL_FEATURES: list[FeatureDefinition] = [
    FeatureDefinition(
        name="hour_of_day",
        dtype="int",
        group="temporal",
        description="Hour of day in local time, 0-23",
    ),
    FeatureDefinition(
        name="day_of_week",
        dtype="int",
        group="temporal",
        description="Day of week, 0=Monday, 6=Sunday",
    ),
    FeatureDefinition(
        name="is_weekend",
        dtype="bool",
        group="temporal",
        description="True if day_of_week >= 5 (Saturday or Sunday)",
    ),
    FeatureDefinition(
        name="month",
        dtype="int",
        group="temporal",
        description="Month of year, 1-12",
    ),
    FeatureDefinition(
        name="is_holiday",
        dtype="bool",
        group="temporal",
        description="True if the snapshot date is a Madrid public holiday",
    ),
    FeatureDefinition(
        name="minutes_since_midnight",
        dtype="int",
        group="temporal",
        description="Minutes elapsed since midnight (0-1439)",
    ),
    FeatureDefinition(
        name="is_rush_hour",
        dtype="bool",
        group="temporal",
        description="True on weekdays 07:00-09:30 or 17:00-20:00",
    ),
]

# ---------------------------------------------------------------------------
# Weather / meteorological features (12)
# ---------------------------------------------------------------------------

WEATHER_FEATURES: list[FeatureDefinition] = [
    FeatureDefinition(
        name="temperature_2m",
        dtype="float",
        group="meteo",
        description="Air temperature at 2 m height, °C",
    ),
    FeatureDefinition(
        name="apparent_temperature",
        dtype="float",
        group="meteo",
        description="Feels-like temperature at 2 m height, °C",
    ),
    FeatureDefinition(
        name="precipitation_mm",
        dtype="float",
        group="meteo",
        description="Precipitation amount in mm",
    ),
    FeatureDefinition(
        name="precipitation_probability",
        dtype="float",
        group="meteo",
        description="Probability of precipitation, 0-100 %",
    ),
    FeatureDefinition(
        name="wind_speed_10m",
        dtype="float",
        group="meteo",
        description="Wind speed at 10 m height, km/h",
    ),
    FeatureDefinition(
        name="is_raining",
        dtype="bool",
        group="meteo",
        description="True if precipitation > 0 mm",
    ),
    FeatureDefinition(
        name="weather_code",
        dtype="int",
        group="meteo",
        description="WMO weather interpretation code",
    ),
    FeatureDefinition(
        name="is_day",
        dtype="bool",
        group="meteo",
        description="True during daytime hours",
    ),
    FeatureDefinition(
        name="direct_radiation",
        dtype="float",
        group="meteo",
        description="Direct solar radiation at surface, W/m²",
    ),
    FeatureDefinition(
        name="feels_cold",
        dtype="bool",
        group="meteo",
        description="True if apparent_temperature < 8°C",
    ),
    FeatureDefinition(
        name="feels_hot",
        dtype="bool",
        group="meteo",
        description="True if apparent_temperature > 30°C",
    ),
    FeatureDefinition(
        name="high_solar_radiation",
        dtype="bool",
        group="meteo",
        description="True if direct_radiation > 400 W/m²",
    ),
]

# ---------------------------------------------------------------------------
# Historical statistics features (5)
# ---------------------------------------------------------------------------

HISTORICAL_FEATURES: list[FeatureDefinition] = [
    FeatureDefinition(
        name="avg_dock_same_hour_7d",
        dtype="float",
        group="stats",
        description="Mean dock_bikes for this station at the same hour over the past 7 days",
    ),
    FeatureDefinition(
        name="std_dock_same_hour_7d",
        dtype="float",
        group="stats",
        description="Std dev of dock_bikes for this station at the same hour over the past 7 days",
    ),
    FeatureDefinition(
        name="avg_dock_same_weekday",
        dtype="float",
        group="stats",
        description="Expanding mean of dock_bikes for this station at the same weekday and hour",
    ),
    FeatureDefinition(
        name="station_daily_turnover",
        dtype="float",
        group="stats",
        description="7-day rolling mean of daily absolute changes in dock_bikes for this station",
    ),
    FeatureDefinition(
        name="dock_bikes_same_time_1w",
        dtype="float",
        group="stats",
        description="dock_bikes at this station exactly 1 week ago at the same timestamp",
    ),
]

# ---------------------------------------------------------------------------
# Static station features (4)
# ---------------------------------------------------------------------------

STATIC_FEATURES: list[FeatureDefinition] = [
    FeatureDefinition(
        name="total_bases",
        dtype="int",
        group="static",
        description="Total number of docking slots at this station",
    ),
    FeatureDefinition(
        name="latitude",
        dtype="float",
        group="static",
        description="Station latitude (WGS84)",
    ),
    FeatureDefinition(
        name="longitude",
        dtype="float",
        group="static",
        description="Station longitude (WGS84)",
    ),
    FeatureDefinition(
        name="distrito",
        dtype="cat",
        group="static",
        description="Madrid district inferred from station coordinates (nullable)",
    ),
]

# ---------------------------------------------------------------------------
# Aggregated catalog
# ---------------------------------------------------------------------------

ALL_FEATURES: list[FeatureDefinition] = (
    LAG_FEATURES + TEMPORAL_FEATURES + WEATHER_FEATURES + HISTORICAL_FEATURES + STATIC_FEATURES
)

FEATURE_NAMES: list[str] = [f.name for f in ALL_FEATURES]


def get_features_by_group(group: str) -> list[FeatureDefinition]:
    """Return all features belonging to the given group.

    Args:
        group: One of "lag", "temporal", "meteo", "stats", "static".

    Returns:
        List of FeatureDefinition objects for that group.
    """
    return [f for f in ALL_FEATURES if f.group == group]
