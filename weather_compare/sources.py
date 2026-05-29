"""Fetch forecasts from independent sources and normalise them.

Two providers are used:

* **Open-Meteo** exposes several independent numerical weather-prediction
  models through one API (ECMWF, NOAA GFS, DWD ICON, Canada GEM, Meteo-France).
  Each model is treated as a separate source — they are genuinely different
  forecasts, which is exactly what we want to compare.
* **MET Norway** (api.met.no) is a fully independent provider; its hourly data
  is aggregated to daily values here.

Every source is fetched defensively: if one fails it is skipped and the others
still produce a comparison.
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from .http import HTTPError, get_json
from .model import DayForecast, Location, SourceForecast

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
MET_NO_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

# Open-Meteo daily variables we request (metric).
_DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "precipitation_probability_max",
    "wind_speed_10m_max",
    "weather_code",
]

# Model id -> (display name, ensemble weight). Weights reflect broad,
# well-documented skill differences; ECMWF/ICON tend to score best.
OPEN_METEO_MODELS: dict[str, tuple[str, float]] = {
    # ifs025 is the 0.25-degree IFS; it has better short-range coverage than
    # ifs04, which can return nulls for the nearest day.
    "ecmwf_ifs025": ("ECMWF IFS", 1.3),
    "gfs_seamless": ("NOAA GFS", 1.0),
    "icon_seamless": ("DWD ICON", 1.2),
    "gem_seamless": ("Canada GEM", 0.9),
    "meteofrance_seamless": ("Meteo-France", 1.0),
}


def fetch_all(
    location: Location,
    *,
    days: int = 7,
    include_metno: bool = True,
    on_error=None,
) -> list[SourceForecast]:
    """Fetch every available source concurrently.

    Sources are fetched in parallel (each is an independent HTTP call), which
    cuts latency from the sum of the per-source round-trips to roughly the
    slowest single one. ``on_error(name, exc)`` is called for sources that fail
    so the caller can warn instead of aborting; results keep a stable order
    (Open-Meteo models in declaration order, then MET Norway).
    """
    jobs: list[tuple[str, callable]] = []
    for model_id, (name, weight) in OPEN_METEO_MODELS.items():
        jobs.append((
            name,
            lambda mid=model_id, n=name, w=weight: fetch_open_meteo_model(
                location, mid, n, w, days),
        ))
    if include_metno:
        jobs.append(("MET Norway", lambda: fetch_met_no(location, days)))

    results: list[SourceForecast | None] = [None] * len(jobs)

    def run(idx: int, name: str, fn) -> None:
        try:
            sf = fn()
            if sf.days:
                results[idx] = sf
        except (HTTPError, KeyError, ValueError, TypeError) as exc:
            if on_error:
                on_error(name, exc)

    with ThreadPoolExecutor(max_workers=len(jobs) or 1) as ex:
        for fut in [ex.submit(run, i, n, fn) for i, (n, fn) in enumerate(jobs)]:
            fut.result()  # surface unexpected (non-handled) errors, if any

    return [sf for sf in results if sf is not None]


def fetch_open_meteo_model(
    location: Location, model_id: str, name: str, weight: float, days: int
) -> SourceForecast:
    data = get_json(
        OPEN_METEO_URL,
        {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "daily": ",".join(_DAILY_VARS),
            "forecast_days": max(1, min(days, 16)),
            "timezone": location.timezone or "auto",
            "models": model_id,
        },
    )
    return SourceForecast(
        name=name, model_id=model_id, weight=weight,
        days=parse_open_meteo_daily(data, model_id),
    )


def parse_open_meteo_daily(data: dict, model_id: str) -> list[DayForecast]:
    """Parse an Open-Meteo daily payload.

    When a model is requested, Open-Meteo may suffix variable names with the
    model id (e.g. ``temperature_2m_max_ecmwf_ifs04``). We look up the suffixed
    key first and fall back to the bare key, so both response shapes work.
    """
    daily = data.get("daily") or {}
    times = daily.get("time") or []

    def col(var: str) -> list:
        return daily.get(f"{var}_{model_id}") or daily.get(var) or []

    tmax = col("temperature_2m_max")
    tmin = col("temperature_2m_min")
    psum = col("precipitation_sum")
    pprob = col("precipitation_probability_max")
    wind = col("wind_speed_10m_max")
    code = col("weather_code")

    out: list[DayForecast] = []
    for i, date in enumerate(times):
        out.append(
            DayForecast(
                date=date,
                temp_max=_at(tmax, i),
                temp_min=_at(tmin, i),
                precip_mm=_at(psum, i),
                precip_prob=_at(pprob, i),
                wind_max=_at(wind, i),
                weather_code=_int_at(code, i),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# MET Norway
# --------------------------------------------------------------------------- #

def fetch_met_no(location: Location, days: int) -> SourceForecast:
    data = get_json(
        MET_NO_URL,
        {"lat": round(location.latitude, 4), "lon": round(location.longitude, 4)},
    )
    return SourceForecast(
        name="MET Norway", model_id="met_no", weight=1.1,
        days=parse_met_no(data, location.timezone, days),
    )


def parse_met_no(data: dict, timezone: str, days: int) -> list[DayForecast]:
    """Aggregate MET Norway's hourly timeseries into daily values.

    Times are UTC; we convert to the location's local date when a tz name is
    available (via :mod:`zoneinfo`), otherwise we group by UTC date.
    """
    tz = _resolve_tz(timezone)
    series = (data.get("properties") or {}).get("timeseries") or []

    temps: dict[str, list[float]] = defaultdict(list)
    precip: dict[str, float] = defaultdict(float)
    codes: dict[str, str] = {}

    for entry in series:
        ts = entry.get("time")
        if not ts:
            continue
        when = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if tz is not None:
            when = when.astimezone(tz)
        date = when.date().isoformat()

        details = (((entry.get("data") or {}).get("instant") or {}).get("details") or {})
        air = details.get("air_temperature")
        if air is not None:
            temps[date].append(float(air))

        nxt = (entry.get("data") or {}).get("next_1_hours") or {}
        amount = (nxt.get("details") or {}).get("precipitation_amount")
        if amount is not None:
            precip[date] += float(amount)
        # Capture a representative symbol for the midday hour.
        symbol = (nxt.get("summary") or {}).get("symbol_code")
        if symbol and 9 <= when.hour <= 15 and date not in codes:
            codes[date] = symbol

    out: list[DayForecast] = []
    for date in sorted(temps)[:days]:
        t = temps[date]
        out.append(
            DayForecast(
                date=date,
                temp_max=round(max(t), 1) if t else None,
                temp_min=round(min(t), 1) if t else None,
                precip_mm=round(precip.get(date, 0.0), 1),
                precip_prob=None,  # met.no compact does not provide PoP
                wind_max=None,
                weather_code=_symbol_to_wmo(codes.get(date)),
            )
        )
    return out


# Map a handful of MET Norway symbol_code prefixes to WMO codes so the
# consensus condition logic can treat met.no like the other sources.
_SYMBOL_WMO = {
    "clearsky": 0, "fair": 1, "partlycloudy": 2, "cloudy": 3,
    "fog": 45, "lightrain": 61, "rain": 63, "heavyrain": 65,
    "lightrainshowers": 80, "rainshowers": 81, "heavyrainshowers": 82,
    "lightsnow": 71, "snow": 73, "heavysnow": 75,
    "lightsnowshowers": 85, "snowshowers": 86,
    "rainandthunder": 95, "heavyrainandthunder": 95,
    "lightsleet": 66, "sleet": 67,
}


def _symbol_to_wmo(symbol: str | None) -> int | None:
    if not symbol:
        return None
    base = symbol.split("_")[0]  # strip _day/_night/_polartwilight
    return _SYMBOL_WMO.get(base)


def _resolve_tz(timezone: str):
    if not timezone or timezone == "auto":
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(timezone)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# tiny helpers
# --------------------------------------------------------------------------- #

def _at(seq: list, i: int):
    return seq[i] if i < len(seq) else None


def _int_at(seq: list, i: int):
    v = _at(seq, i)
    return int(v) if v is not None else None
