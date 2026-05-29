"""Minimal HTTP helper built on the standard library.

Kept dependency-free on purpose so the tool runs with a bare Python install
(no ``pip install`` step). Provides a JSON GET with a timeout, a User-Agent
(required by some providers such as MET Norway) and a small retry/backoff loop
for transient network errors.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

USER_AGENT = "weather-compare/1.0 (https://github.com/; contact via repository)"

DEFAULT_TIMEOUT = 15
DEFAULT_RETRIES = 3


class HTTPError(Exception):
    """Raised when a request ultimately fails after retries."""


def get_json(
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> Any:
    """GET ``url`` (optionally with query ``params``) and parse JSON.

    Retries transient failures with exponential backoff. Raises :class:`HTTPError`
    if every attempt fails or the body is not valid JSON.
    """
    if params:
        # Drop ``None`` values so callers can pass optional params cleanly.
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{url}?{urllib.parse.urlencode(clean)}"

    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            # 4xx (other than 429) are not worth retrying.
            if exc.code != 429 and 400 <= exc.code < 500:
                raise HTTPError(f"{url} -> HTTP {exc.code} {exc.reason}") from exc
            last_err = exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_err = exc

        if attempt < retries - 1:
            time.sleep(2 ** attempt)  # 1s, 2s, 4s ...

    raise HTTPError(f"request failed after {retries} attempts: {url} ({last_err})")
