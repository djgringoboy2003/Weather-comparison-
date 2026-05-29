"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .compare import build_consensus
from .geocode import LocationNotFound, geocode
from .render import render_json, render_report
from .sources import fetch_all


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="weather-compare",
        description="Compare several weather sources and predict the most "
                    "likely forecast for the week ahead.",
    )
    p.add_argument("location", help='Place to forecast, e.g. "Tokyo" or "Paris, France".')
    p.add_argument("-d", "--days", type=int, default=7,
                   help="Number of forecast days (1-16, default 7).")
    p.add_argument("-u", "--units", choices=["metric", "imperial"], default="metric",
                   help="Unit system (default metric).")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Show each source's values, not just the consensus.")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    p.add_argument("--no-metno", action="store_true",
                   help="Skip the MET Norway source.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not 1 <= args.days <= 16:
        print("error: --days must be between 1 and 16", file=sys.stderr)
        return 2

    try:
        location = geocode(args.location)
    except LocationNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    warnings: list[str] = []
    sources = fetch_all(
        location,
        days=args.days,
        include_metno=not args.no_metno,
        on_error=lambda name, exc: warnings.append(f"{name}: {exc}"),
    )

    if not sources:
        print("error: no weather sources were reachable. Check your network "
              "connection / firewall allowlist.", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
        return 1

    consensus = build_consensus(sources)[: args.days]

    if args.json:
        print(render_json(location, sources, consensus))
    else:
        print(render_report(
            location, sources, consensus,
            imperial=args.units == "imperial", verbose=args.verbose,
        ))
        if warnings:
            print("  Note: some sources were unavailable and were skipped:",
                  file=sys.stderr)
            for w in warnings:
                print(f"    - {w}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
