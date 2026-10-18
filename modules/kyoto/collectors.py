"""Collectors: Kyoto HAPI -> `kyoto.dst` (NOAA's mirror as fallback, D-081)
and `kyoto.ae` (delayed, D-082). Both products are revisable (D-080), so
re-fetching a window that overlaps the store updates values Kyoto has
revised and leaves the rest alone."""

from __future__ import annotations

import datetime as dt
import logging

import httpx
from django.utils import timezone

from core import swxkit
from modules.swpc import client as swpc_client

from . import adapters, client

logger = logging.getLogger(__name__)

DST_ID = "kyoto.dst"
DST_DATASET = "hour_dst_realtime"
DST_LOOKBACK = dt.timedelta(hours=8)
NOAA_MIRROR = "products/kyoto-dst.json"

AE_ID = "kyoto.ae"
AE_DATASET = "min_ae"
AE_LOOKBACK = dt.timedelta(days=40)


def fetch_rows(dataset: str, start: dt.datetime, stop: dt.datetime) -> list[dict]:
    meta, text = client.fetch(dataset, start, stop)
    return adapters.parse_csv(meta, text)


def collect_dst(now: dt.datetime | None = None) -> int:
    """Kyoto's realtime Dst for the last 8 h; if that fails or is empty,
    NOAA's mirror of it. Each row's `source` names the route it came from."""
    now = now or timezone.now()
    try:
        rows = fetch_rows(DST_DATASET, now - DST_LOOKBACK, now)
        reason = "" if rows else "Kyoto HAPI returned no rows"
    except (client.HapiError, ValueError) as exc:
        rows, reason = [], f"Kyoto HAPI: {exc}"
    if rows:
        return swxkit.publish_timeseries(DST_ID, rows)
    logger.warning("%s: %s; using NOAA's mirror", DST_ID, reason)
    try:
        rows = adapters.parse_noaa_mirror(swpc_client.fetch_json(NOAA_MIRROR))
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        swxkit.heartbeat(DST_ID, "error", detail=f"{reason}; NOAA mirror: {exc}"[:500])
        return 0
    return swxkit.publish_timeseries(DST_ID, rows)


def collect_ae(now: dt.datetime | None = None) -> int:
    """`min_ae` (best available per minute) for the last 40 days, daily.
    Kyoto's HAPI copy runs weeks behind its quicklook page, so this is
    history and training data, not a live input."""
    now = now or timezone.now()
    try:
        rows = fetch_rows(AE_DATASET, now - AE_LOOKBACK, now)
    except (client.HapiError, ValueError) as exc:
        swxkit.heartbeat(AE_ID, "error", detail=f"Kyoto HAPI: {exc}"[:500])
        return 0
    return swxkit.publish_timeseries(AE_ID, rows)


def backfill(product_id: str, dataset: str, since: dt.datetime, until: dt.datetime,
             chunk: dt.timedelta, write=None) -> int:
    """One-off backfill/repair: `dataset` from `since` to `until`, in chunks,
    each published with `revise_since` so an older stored value is revised
    too. Raises HapiError: a person is watching this one."""
    total, start = 0, since
    while start < until:
        stop = min(start + chunk, until)
        rows = fetch_rows(dataset, start, stop)
        if rows:
            total += swxkit.publish_timeseries(product_id, rows, revise_since=start)
        if write:
            write(f"{start:%Y-%m-%d} .. {stop:%Y-%m-%d}: {len(rows)} row(s) fetched")
        start = stop
    return total


COLLECTORS = {
    DST_ID: collect_dst,
    AE_ID: collect_ae,
}
