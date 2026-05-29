"""Shared data structures and WMO weather-code helpers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DayForecast:
    """A single day's forecast from one source, in metric units."""

    date: str  # ISO date, e.g. "2026-05-29"
    temp_max: float | None = None  # degrees C
    temp_min: float | None = None  # degrees C
    precip_mm: float | None = None  # total precipitation, mm
    precip_prob: float | None = None  # 0-100, may be None if source lacks it
    wind_max: float | None = None  # km/h
    weather_code: int | None = None  # WMO code


@dataclass
class SourceForecast:
    """All daily forecasts from a single source/model."""

    name: str  # human label, e.g. "ECMWF IFS"
    model_id: str  # internal id, e.g. "ecmwf_ifs04"
    weight: float = 1.0  # relative skill weight in the ensemble
    days: list[DayForecast] = field(default_factory=list)


@dataclass
class Location:
    name: str
    latitude: float
    longitude: float
    country: str | None = None
    admin1: str | None = None
    timezone: str = "auto"

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.admin1 and self.admin1 != self.name:
            parts.append(self.admin1)
        if self.country:
            parts.append(self.country)
        return ", ".join(parts)


# WMO weather interpretation codes -> (description, coarse category).
# Categories: clear, cloud, fog, drizzle, rain, snow, storm.
_WMO: dict[int, tuple[str, str]] = {
    0: ("Clear sky", "clear"),
    1: ("Mainly clear", "clear"),
    2: ("Partly cloudy", "cloud"),
    3: ("Overcast", "cloud"),
    45: ("Fog", "fog"),
    48: ("Depositing rime fog", "fog"),
    51: ("Light drizzle", "drizzle"),
    53: ("Moderate drizzle", "drizzle"),
    55: ("Dense drizzle", "drizzle"),
    56: ("Light freezing drizzle", "drizzle"),
    57: ("Dense freezing drizzle", "drizzle"),
    61: ("Slight rain", "rain"),
    63: ("Moderate rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Light freezing rain", "rain"),
    67: ("Heavy freezing rain", "rain"),
    71: ("Slight snow", "snow"),
    73: ("Moderate snow", "snow"),
    75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"),
    80: ("Slight rain showers", "rain"),
    81: ("Moderate rain showers", "rain"),
    82: ("Violent rain showers", "rain"),
    85: ("Slight snow showers", "snow"),
    86: ("Heavy snow showers", "snow"),
    95: ("Thunderstorm", "storm"),
    96: ("Thunderstorm with slight hail", "storm"),
    99: ("Thunderstorm with heavy hail", "storm"),
}


def describe_code(code: int | None) -> str:
    if code is None:
        return "Unknown"
    return _WMO.get(int(code), ("Unknown", "cloud"))[0]


def code_category(code: int | None) -> str:
    if code is None:
        return "unknown"
    return _WMO.get(int(code), ("Unknown", "cloud"))[1]
