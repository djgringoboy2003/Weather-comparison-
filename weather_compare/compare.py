"""Combine multiple sources into a single consensus forecast per day.

The "better prediction" is a weighted ensemble: for each day we gather every
source's value for each variable, take a skill-weighted mean for continuous
quantities, and measure the spread between sources. Tight agreement -> high
confidence; wide disagreement -> low confidence. Precipitation likelihood blends
the fraction of sources predicting measurable rain with any reported
probability-of-precipitation values.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .model import SourceForecast, code_category

# Std-dev thresholds (deg C) for the temperature-agreement confidence band.
# We score agreement on the standard deviation of the per-source highs, not the
# raw max-min range: the range only sees the two most extreme models and grows
# as more models are added, so a single outlier (or simply having 6 sources)
# would unfairly push confidence down even when most models agree closely.
_TEMP_STD_HIGH = 1.0
_TEMP_STD_MED = 2.0
# A day is "wet" for a source if it predicts at least this much precip.
_WET_MM = 0.5


@dataclass
class SourceValue:
    source: str
    value: float | None


@dataclass
class DayConsensus:
    date: str
    temp_max: float | None
    temp_min: float | None
    temp_max_spread: float | None  # max-min across sources, deg C
    precip_mm: float | None
    precip_chance: int | None  # 0-100
    condition: str  # human readable
    condition_category: str
    confidence: str  # High / Medium / Low
    n_sources: int
    # Per-source values for the verbose breakdown.
    per_source_tmax: list[SourceValue] = field(default_factory=list)
    per_source_precip: list[SourceValue] = field(default_factory=list)


def build_consensus(sources: list[SourceForecast]) -> list[DayConsensus]:
    """Align sources by date and produce one :class:`DayConsensus` per day.

    Only dates covered by at least one source are returned, in chronological
    order. Days are not dropped for missing sources — the ensemble simply uses
    whoever is available.
    """
    by_date: dict[str, list[tuple[SourceForecast, "DayForecast"]]] = {}
    for src in sources:
        for day in src.days:
            by_date.setdefault(day.date, []).append((src, day))

    out: list[DayConsensus] = []
    for date in sorted(by_date):
        out.append(_consensus_for_day(date, by_date[date]))
    return out


def _consensus_for_day(date, entries) -> DayConsensus:
    tmax_vals, tmax_weights, tmax_per = [], [], []
    tmin_vals, tmin_weights = [], []
    precip_vals, precip_weights, precip_per = [], [], []
    prob_vals = []
    wet_flags = []
    categories = []

    for src, day in entries:
        if day.temp_max is not None:
            tmax_vals.append(day.temp_max)
            tmax_weights.append(src.weight)
        tmax_per.append(SourceValue(src.name, day.temp_max))

        if day.temp_min is not None:
            tmin_vals.append(day.temp_min)
            tmin_weights.append(src.weight)

        if day.precip_mm is not None:
            precip_vals.append(day.precip_mm)
            precip_weights.append(src.weight)
            wet_flags.append(day.precip_mm >= _WET_MM)
        precip_per.append(SourceValue(src.name, day.precip_mm))

        if day.precip_prob is not None:
            prob_vals.append(day.precip_prob)

        if day.weather_code is not None:
            categories.append(code_category(day.weather_code))

    temp_max = _wmean(tmax_vals, tmax_weights)
    temp_min = _wmean(tmin_vals, tmin_weights)
    spread = (max(tmax_vals) - min(tmax_vals)) if len(tmax_vals) >= 2 else (0.0 if tmax_vals else None)
    precip_mm = _wmean(precip_vals, precip_weights)

    chance = _precip_chance(wet_flags, prob_vals)
    condition, category = _consensus_condition(categories, chance)
    confidence = _confidence(tmax_vals, wet_flags)

    return DayConsensus(
        date=date,
        temp_max=_round(temp_max),
        temp_min=_round(temp_min),
        temp_max_spread=_round(spread),
        precip_mm=_round(precip_mm),
        precip_chance=chance,
        condition=condition,
        condition_category=category,
        confidence=confidence,
        n_sources=len(entries),
        per_source_tmax=tmax_per,
        per_source_precip=precip_per,
    )


def _precip_chance(wet_flags: list[bool], prob_vals: list[float]) -> int | None:
    """Blend the share of sources predicting rain with reported PoP values."""
    if not wet_flags and not prob_vals:
        return None
    parts = []
    if wet_flags:
        parts.append(100.0 * sum(wet_flags) / len(wet_flags))
    if prob_vals:
        parts.append(statistics.fmean(prob_vals))
    return int(round(statistics.fmean(parts)))


def _consensus_condition(categories: list[str], chance: int | None) -> tuple[str, str]:
    """Pick the most common WMO category, but let a strong precip signal win."""
    if categories:
        category = statistics.mode(categories)
    elif chance is not None and chance >= 50:
        category = "rain"
    else:
        category = "cloud"

    # If most sources call it dry but the blended chance is high (or vice
    # versa), trust the precipitation signal for the headline label.
    if chance is not None:
        if chance >= 60 and category in ("clear", "cloud", "fog"):
            category = "rain"
        elif chance <= 20 and category in ("rain", "drizzle"):
            category = "cloud"

    return _CATEGORY_LABEL.get(category, "Mixed"), category


_CATEGORY_LABEL = {
    "clear": "Clear / sunny",
    "cloud": "Cloudy",
    "fog": "Fog",
    "drizzle": "Drizzle",
    "rain": "Rain likely",
    "snow": "Snow likely",
    "storm": "Thunderstorms",
    "unknown": "Mixed",
}


def _confidence(tmax_vals: list[float], wet_flags: list[bool]) -> str:
    """High when sources agree on both temperature and wet/dry; degraded by
    disagreement on either axis."""
    if len(tmax_vals) < 2:
        return "Low"  # single source -> nothing to corroborate

    std = statistics.pstdev(tmax_vals)
    if std <= _TEMP_STD_HIGH:
        temp_band = "High"
    elif std <= _TEMP_STD_MED:
        temp_band = "Medium"
    else:
        temp_band = "Low"

    # Fraction agreeing with the majority wet/dry call.
    if wet_flags:
        wet = sum(wet_flags)
        agree = max(wet, len(wet_flags) - wet) / len(wet_flags)
        precip_band = "High" if agree >= 0.8 else "Medium" if agree >= 0.6 else "Low"
    else:
        precip_band = temp_band

    order = {"High": 3, "Medium": 2, "Low": 1}
    return min(temp_band, precip_band, key=lambda b: order[b])


def _wmean(values: list[float], weights: list[float]) -> float | None:
    if not values:
        return None
    total_w = sum(weights)
    if total_w <= 0:
        return statistics.fmean(values)
    return sum(v * w for v, w in zip(values, weights)) / total_w


def _round(v: float | None, ndigits: int = 1) -> float | None:
    return round(v, ndigits) if v is not None else None
