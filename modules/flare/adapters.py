"""Parses flare-nrt's `/api/status` response (shape read directly from
`HelioUSACH/flare-nrt`'s `nrt_server_v3.py::api_status`, 2026-10 — re-sync
this adapter and its fixture if that endpoint's shape changes, same as any
other external adapter in this repo).

The fields this module reads (D-096):

- `live_status`: the producer's own freshness, computed per request:
  `"live"`, `"stale"` (its inputs or loop stopped; `seconds_since_update`
  says for how long), `"no_data"` (nothing produced yet) or `"unknown"`.
  A missing or unrecognised value is read as `"unknown"`; `"error"` is
  passed through. Only `"live"` is ever published (`collectors.py`).
- `timestamp`: when the result was made, ISO 8601. The producer currently
  sends it **naive** (`2026-10-15T01:59:21.483912`), meaning UTC; `Z` and
  explicit offsets are accepted too. It becomes the event's `issued`, which
  the collector uses to skip a result it has already stored (the producer
  updates every ~3 min, the collector polls every minute).
- `seconds_since_update`: reported in the heartbeat detail when stale.
- `probability`, `flare_detected`, `location`, `ensemble_std`: the result.

Deliberately **not** surfaced in the public event payload: `severity`
(01-AUDIT.md §3.6 — self-reports accuracy 0.39, "exploratory_low"; showing
it as if it were a usable signal would be dishonest, D-036/D-044 "honest
science"). It stays available to the flare-nrt service's own expert
dashboard, just not republished here."""

from __future__ import annotations

import datetime as dt

LIVE_STATUSES = frozenset({"live", "stale", "no_data", "unknown", "error"})


def live_status(raw: dict) -> str:
    status = raw.get("live_status")
    return status if status in LIVE_STATUSES else "unknown"


def parse_time(value: str) -> dt.datetime:
    """ISO 8601 -> aware UTC datetime; a naive string is UTC."""
    when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if when.tzinfo is None:
        return when.replace(tzinfo=dt.UTC)
    return when.astimezone(dt.UTC)


def parse_status(raw: dict) -> dict | None:
    """Returns None when the service hasn't produced a result yet (fresh
    start, `last_update` still null) — the collector then skips publishing
    rather than inventing a zero-confidence event."""
    if not raw.get("timestamp"):
        return None
    issued = parse_time(raw["timestamp"])
    return {
        "issued": issued,
        "valid_from": issued,
        "payload": {
            "probability": raw.get("probability", 0.0),
            "flare_detected": bool(raw.get("flare_detected", False)),
            "location": raw.get("location"),
        },
        "quality": {
            "producer_live_status": live_status(raw),
            "ensemble_std": raw.get("ensemble_std"),
        },
    }
