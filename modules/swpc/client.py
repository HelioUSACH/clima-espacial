"""Thin HTTP client for NOAA SWPC's JSON products. One place to change the
base URL, timeout and redirect policy (D-040: JSOC's plain http:// answers
with a 301 — not SWPC today, but follow redirects everywhere on principle)."""

from __future__ import annotations

import httpx

BASE_URL = "https://services.swpc.noaa.gov"
TIMEOUT = 10.0


def fetch_json(path: str):
    url = f"{BASE_URL}/{path.lstrip('/')}"
    response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    return response.json()
