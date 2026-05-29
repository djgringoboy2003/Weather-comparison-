"""Weather comparison & consensus prediction.

Fetches the same forecast from several independent weather models / providers,
aligns them day-by-day, and produces a single "most likely" prediction together
with a confidence rating derived from how much the sources agree.
"""

__version__ = "1.0.0"
