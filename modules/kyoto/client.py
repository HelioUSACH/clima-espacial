"""Thin HAPI 3.x client for WDC Kyoto's server (`/hapi/info`, `/hapi/data`).

CSV is HAPI's default data format and needs nothing beyond `csv`; no
`hapiclient` (it pulls in pandas/numpy). httpx is the portal's existing
HTTP client (modules/swpc). One retry on a network error or a 5xx; a HAPI
error status (e.g. 1405, a stop after the server's stopDate) raises
HapiError with the server's own message, for the collector to turn into a
heartbeat instead of a crash."""

from __future__ import annotations

import datetime as dt

import httpx
from django.conf import settings

BASE_URL = "https://wdc.kugi.kyoto-u.ac.jp/hapi"
TIMEOUT = 30.0
REPO_URL = "https://github.com/HelioUSACH/clima-espacial"


class HapiError(Exception):
    """The server couldn't serve the request (network, HTTP or HAPI status)."""


def user_agent() -> str:
    contact = getattr(settings, "SITE_CONTACT_EMAIL", "")
    return f"ClimaEspacialUSACH (+{REPO_URL}{'; ' + contact if contact else ''})"


def _get(path: str, params: dict) -> httpx.Response:
    last = None
    for _attempt in range(2):
        try:
            response = httpx.get(f"{BASE_URL}/{path}", params=params, timeout=TIMEOUT, follow_redirects=True,
                                 headers={"User-Agent": user_agent()})
        except httpx.HTTPError as exc:
            last = HapiError(f"{path}: {exc.__class__.__name__}: {exc}")
            continue
        if response.status_code >= 500:
            last = HapiError(f"{path}: HTTP {response.status_code}")
            continue
        if response.status_code >= 400:
            raise HapiError(f"{path}: {_status_message(response)}")
        return response
    raise last


def _status_message(response: httpx.Response) -> str:
    try:
        status = response.json()["status"]
        return f"HAPI {status['code']} {status['message']}"
    except (ValueError, KeyError, TypeError):
        return f"HTTP {response.status_code}"


def _iso(when: dt.datetime) -> str:
    return when.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def info(dataset: str) -> dict:
    data = _get("info", {"dataset": dataset}).json()
    if data.get("status", {}).get("code") != 1200:
        raise HapiError(f"info {dataset}: {data.get('status')}")
    return data


def fetch(dataset: str, start: dt.datetime, stop: dt.datetime) -> tuple[dict, str]:
    """(info, CSV text) for [start, stop), with `stop` clamped to the
    dataset's `stopDate` (a later stop is a 1405 error). Empty CSV when the
    clamped range is empty or the server has nothing in it."""
    meta = info(dataset)
    stop = min(stop, parse_time(meta["stopDate"]))
    start = max(start, parse_time(meta["startDate"]))
    if start >= stop:
        return meta, ""
    text = _get("data", {"dataset": dataset, "start": _iso(start), "stop": _iso(stop)}).text
    return meta, text
