"""Turn a free-text location into coordinates via Open-Meteo geocoding."""

from __future__ import annotations

from .http import HTTPError, get_json
from .model import Location

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


class LocationNotFound(Exception):
    pass


def geocode(query: str, *, language: str = "en") -> Location:
    """Resolve ``query`` (e.g. "Tokyo" or "Paris, France") to a :class:`Location`.

    Raises :class:`LocationNotFound` if nothing matches.
    """
    query = query.strip()
    if not query:
        raise LocationNotFound("empty location")

    # The geocoder matches on the place name only, so search on the part before
    # the first comma and use the rest (country/region) to disambiguate.
    primary = query.split(",")[0].strip()
    try:
        data = get_json(
            GEOCODE_URL,
            {"name": primary, "count": 10, "language": language, "format": "json"},
        )
    except HTTPError as exc:
        raise LocationNotFound(f"geocoding request failed: {exc}") from exc

    results = data.get("results") or []
    if not results:
        raise LocationNotFound(f"no location found for {query!r}")

    best = _pick_best(results, query)
    return Location(
        name=best.get("name", primary),
        latitude=float(best["latitude"]),
        longitude=float(best["longitude"]),
        country=best.get("country"),
        admin1=best.get("admin1"),
        timezone=best.get("timezone", "auto"),
    )


def _pick_best(results: list[dict], query: str) -> dict:
    """Prefer a result whose country/region also appears in the query."""
    hint = query.lower()
    for r in results:
        tokens = [str(r.get(k, "")).lower() for k in ("country", "admin1", "country_code")]
        if any(tok and tok in hint for tok in tokens):
            return r
    # Otherwise the first result is the highest-population/best match.
    return results[0]
