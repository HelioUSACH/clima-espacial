"""Client for the flare-nrt producer's own status endpoint (not exposed
publicly — docs/ARCHITECTURE.md §6: 127.0.0.1:5050, nginx never proxies it).
Configurable because it isn't running in dev/CI."""

from __future__ import annotations

import os

import httpx

BASE_URL = os.environ.get("FLARE_NRT_URL", "http://127.0.0.1:5050")
TIMEOUT = 5.0


def fetch_status():
    response = httpx.get(f"{BASE_URL}/api/status", timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()
