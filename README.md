# Weather Comparison & Consensus Predictor

Scrapes the **same forecast from several independent weather models**, lines them
up day-by-day for the week ahead (up to 16 days), and produces a single
**"most likely" prediction** with a **confidence rating** based on how strongly
the sources agree.

The idea: any one model can be wrong, but when ECMWF, NOAA GFS, DWD ICON, Canada
GEM, Météo-France and MET Norway all point the same way, the forecast is far more
trustworthy than any single app. When they disagree, the tool tells you that too
— so a "High" confidence sunny day means something different from a "Low"
confidence one.

## Sources compared

| Source | Provider | Notes |
|--------|----------|-------|
| ECMWF IFS | Open-Meteo | Generally the most skillful global model (weighted highest) |
| NOAA GFS | Open-Meteo | US global model |
| DWD ICON | Open-Meteo | German weather service global model |
| Canada GEM | Open-Meteo | Environment Canada model |
| Météo-France | Open-Meteo | French weather service model |
| MET Norway | api.met.no | Fully independent provider (hourly data aggregated to daily) |

All sources are **free and need no API key**. The program uses only the Python
standard library — there is nothing to `pip install`.

## Requirements

- Python 3.9+
- Outbound network access to `api.open-meteo.com`, `geocoding-api.open-meteo.com`
  and `api.met.no`.

> **Running inside Claude Code on the web?** The sandbox's network policy may
> block these hosts (you'll see `403 Forbidden` / "Host not in allowlist").
> Run it on your own machine, or start a web session with a network policy that
> allows those hosts. See
> https://code.claude.com/docs/en/claude-code-on-the-web for how environment
> network policies work.

## Usage

```bash
# 7-day consensus for a place
python3 -m weather_compare "Tokyo"

# 10 days, Fahrenheit/inches, with each source's numbers shown
python3 -m weather_compare "Denver, Colorado" --days 10 --units imperial --verbose

# Machine-readable output
python3 -m weather_compare "Paris, France" --json

# Skip the MET Norway source
python3 -m weather_compare "London" --no-metno
```

### Options

| Flag | Description |
|------|-------------|
| `-d`, `--days N` | Forecast horizon, 1–16 (default 7) |
| `-u`, `--units {metric,imperial}` | Units (default metric: °C, mm, km/h) |
| `-v`, `--verbose` | Show every source's value per day, not just the consensus |
| `--json` | Emit JSON instead of the table |
| `--no-metno` | Skip the MET Norway source |

### Sample output

```
  Weather consensus for Tokyo, Japan
  35.690, 139.690  ·  tz Asia/Tokyo
  Combining 6 sources: ECMWF IFS, NOAA GFS, DWD ICON, Canada GEM, Meteo-France, MET Norway

  Day  Date          Hi   Lo   Range   Rain   Precip  Outlook          Confidence
  -------------------------------------------------------------------------------
  Fri  2026-05-29    22   13      ±2    20%    0.0 mm Clear / sunny    ●●● High
  Sat  2026-05-30    23   14      ±4    66%    2.7 mm Rain likely      ●●○ Medium
  ...
```

## How the consensus is computed

For each day the engine (`weather_compare/compare.py`):

1. **Aligns** every source's forecast by calendar date.
2. **Temperature** → skill-weighted mean across sources. The spread
   (warmest − coldest source) is shown as `±Range`.
3. **Precipitation chance** → blends the *share of sources predicting measurable
   rain* with any *probability-of-precipitation* values the sources report.
4. **Outlook** → the most common weather category, overridden when a strong
   precipitation signal contradicts it.
5. **Confidence** → `High` / `Medium` / `Low`, the *lower* of:
   - temperature agreement (spread ≤1.5 °C → High, ≤3 °C → Medium, else Low), and
   - wet/dry agreement (≥80 % of sources agree → High, ≥60 % → Medium, else Low).

   A single available source is always `Low` — there is nothing to corroborate it.

If a source is temporarily unavailable it is skipped with a warning; the
consensus is built from whoever responded.

## Project layout

```
weather_compare/
  http.py       stdlib HTTP GET with timeout + retry/backoff
  geocode.py    place name -> latitude/longitude (Open-Meteo geocoder)
  sources.py    fetch + normalise each model/provider
  compare.py    alignment, weighted ensemble, confidence scoring
  render.py     table / JSON output, unit conversion
  model.py      dataclasses + WMO weather-code lookup
  cli.py        argument parsing / entry point
tests/          unittest suite (parsing + consensus logic, fixture-based)
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The suite runs fully offline using fixture payloads, so the comparison logic and
both providers' parsers are verified without hitting the network.

## Notes & limitations

- Forecasts beyond ~10 days are inherently low-skill; the confidence rating will
  usually reflect that with wider spreads.
- "Most likely" is a statistical consensus, not a guarantee — treat `Low`
  confidence days as genuinely uncertain.
- Adding more providers is just another `fetch_*` function in `sources.py` that
  returns a `SourceForecast`.
