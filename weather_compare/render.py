"""Render a consensus forecast as a human-readable report or JSON."""

from __future__ import annotations

import datetime as _dt
import json

from .compare import DayConsensus
from .model import Location, SourceForecast


def c_to_f(c: float | None) -> float | None:
    return round(c * 9 / 5 + 32, 1) if c is not None else None


def kmh_to_mph(k: float | None) -> float | None:
    return round(k * 0.621371, 1) if k is not None else None


def mm_to_in(mm: float | None) -> float | None:
    return round(mm / 25.4, 2) if mm is not None else None


def _temp(c: float | None, imperial: bool) -> str:
    if c is None:
        return "  -"
    return f"{c_to_f(c):.0f}" if imperial else f"{c:.0f}"


def _weekday(date: str) -> str:
    try:
        return _dt.date.fromisoformat(date).strftime("%a")
    except ValueError:
        return "?"


_CONF_MARK = {"High": "●●●", "Medium": "●●○", "Low": "●○○"}


def render_report(
    location: Location,
    sources: list[SourceForecast],
    consensus: list[DayConsensus],
    *,
    imperial: bool = False,
    verbose: bool = False,
) -> str:
    unit_t = "°F" if imperial else "°C"
    unit_p = "in" if imperial else "mm"
    lines: list[str] = []

    lines.append("")
    lines.append(f"  Weather consensus for {location.label}")
    lines.append(f"  {location.latitude:.3f}, {location.longitude:.3f}  ·  tz {location.timezone}")
    lines.append(
        f"  Combining {len(sources)} sources: "
        + ", ".join(s.name for s in sources)
    )
    lines.append("")

    header = (
        f"  {'Day':<4} {'Date':<11} {'Hi':>4} {'Lo':>4}  "
        f"{'Range':>6}  {'Rain':>5}  {'Precip':>7}  {'Outlook':<16} Confidence"
    )
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))

    for day in consensus:
        rng = f"±{day.temp_max_spread:.0f}" if day.temp_max_spread is not None else "  -"
        chance = f"{day.precip_chance}%" if day.precip_chance is not None else "  -"
        if day.precip_mm is None:
            precip = "    -"
        else:
            val = mm_to_in(day.precip_mm) if imperial else day.precip_mm
            precip = f"{val:.2f}" if imperial else f"{val:.1f}"
        lines.append(
            f"  {_weekday(day.date):<4} {day.date:<11} "
            f"{_temp(day.temp_max, imperial):>4} {_temp(day.temp_min, imperial):>4}  "
            f"{rng:>6}  {chance:>5}  {precip:>5} {unit_p:<2} {day.condition:<16} "
            f"{_CONF_MARK.get(day.confidence, '?')} {day.confidence}"
        )

    lines.append("")
    lines.append(f"  Temperatures in {unit_t}. 'Range' = hi-temp spread across sources "
                 f"(smaller = stronger agreement).")
    lines.append("  Confidence: ●●● High  ●●○ Medium  ●○○ Low — based on how closely the "
                 "sources agree.")

    if verbose:
        lines.append("")
        lines.append("  Per-source high temperatures (" + unit_t + "):")
        for day in consensus:
            vals = ", ".join(
                f"{sv.source}={_temp(sv.value, imperial)}"
                for sv in day.per_source_tmax
                if sv.value is not None
            )
            lines.append(f"    {day.date}: {vals}")

    lines.append("")
    return "\n".join(lines)


def to_dict(
    location: Location, sources: list[SourceForecast], consensus: list[DayConsensus]
) -> dict:
    return {
        "location": {
            "name": location.label,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "timezone": location.timezone,
        },
        "sources": [s.name for s in sources],
        "forecast": [
            {
                "date": d.date,
                "temp_max_c": d.temp_max,
                "temp_min_c": d.temp_min,
                "temp_max_spread_c": d.temp_max_spread,
                "precip_mm": d.precip_mm,
                "precip_chance_pct": d.precip_chance,
                "condition": d.condition,
                "condition_category": d.condition_category,
                "confidence": d.confidence,
                "n_sources": d.n_sources,
                "per_source_temp_max_c": {
                    sv.source: sv.value for sv in d.per_source_tmax
                },
            }
            for d in consensus
        ],
    }


def render_json(
    location: Location, sources: list[SourceForecast], consensus: list[DayConsensus]
) -> str:
    return json.dumps(to_dict(location, sources, consensus), indent=2)
