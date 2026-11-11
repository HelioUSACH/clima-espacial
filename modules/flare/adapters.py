"""Parses flare-nrt's `/api/status` response. The contract is the
"GET /api/status" section of `HelioUSACH/flare-nrt`'s `INTEGRATION.md`
(v1.0.0); `tests/fixtures/status_v1_*.json` follow that shape field for
field. Re-sync this adapter and its fixtures if that section changes, same
as any other external adapter in this repo.

The fields this module reads:

- `live_status` (D-096): the producer's own freshness, computed per
  request: `"live"`, `"stale"` (`seconds_since_update` says for how long),
  `"no_data"` or `"unknown"`. A missing or unrecognised value is read as
  `"unknown"`; `"error"` is passed through. Only `"live"` is ever published
  (`collectors.py`).
- `timestamp`: when the result was made, unique per result. It is the
  event's `issued`, which the collector uses to skip a result it has already
  stored (D-096). v1.0.0 sends ISO 8601 with `Z`; a naive string (what
  versions before 1.0 sent) is read as UTC.
- `observation_time`: the AIA slot the result is about. It is the event's
  `valid_from` (D-098), so the portal's times are observation times, not
  processing times. A result without it (a producer before 1.0) falls back
  to `timestamp`.
- The result, into the payload: `probability`, `threshold`, `alert`,
  `state` (`quiet`/`onset`/`active`/`decay`), `flare_detected`, `event_id`,
  `event_t_start` and `location` (experimental, only while an event is
  open; only its documented keys are kept).
- Into quality: `live_status`, `input_age_s`, `ensemble_std`.
- Into provenance: `model_version` (as `flare-nrt@<version>`) and `inputs`,
  the provider time (T_OBS) of each of the four AIA channels used.

Not republished: `severity` (01-AUDIT.md §3.6: it self-reported accuracy
0.39, "exploratory_low"; flare-nrt 1.0 no longer sends it), and the
per-member `ensemble_scores`, loop health and latency fields, which describe
the service, not the result."""

from __future__ import annotations

import datetime as dt

LIVE_STATUSES = frozenset({"live", "stale", "no_data", "unknown", "error"})
STATES = frozenset({"quiet", "onset", "active", "decay"})
CHANNELS = ("131", "171", "304", "1600")
# INTEGRATION.md "location": the documented keys, nothing else.
LOCATION_KEYS = ("centroid_arcsec", "bbox_arcsec", "area_px", "area_arcsec2", "on_limb", "method", "confidence")


def live_status(raw: dict) -> str:
    status = raw.get("live_status")
    return status if status in LIVE_STATUSES else "unknown"


def parse_time(value: str) -> dt.datetime:
    """ISO 8601 -> aware UTC datetime; a naive string is UTC."""
    when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if when.tzinfo is None:
        return when.replace(tzinfo=dt.UTC)
    return when.astimezone(dt.UTC)


def _iso(value) -> str | None:
    """A producer time, normalised to ISO 8601 UTC with `Z`, or None."""
    if not value:
        return None
    try:
        when = parse_time(value)
    except (TypeError, ValueError, AttributeError):
        return None
    spec = "milliseconds" if when.microsecond else "seconds"
    return when.isoformat(timespec=spec).replace("+00:00", "Z")


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _location(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    return {k: raw[k] for k in LOCATION_KEYS if k in raw} or None


def _inputs(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {w: _iso(raw.get(w) or raw.get(int(w))) for w in CHANNELS if raw.get(w) or raw.get(int(w))}


def parse_status(raw: dict) -> dict | None:
    """Returns None when the service hasn't produced a result yet (fresh
    start, no `timestamp`): the collector then skips publishing rather than
    inventing a zero-confidence event."""
    if not raw.get("timestamp"):
        return None
    issued = parse_time(raw["timestamp"])
    observed = raw.get("observation_time")
    valid_from = parse_time(observed) if observed else issued
    state = raw.get("state")
    version = raw.get("model_version")
    return {
        "issued": issued,
        "valid_from": valid_from,
        "payload": {
            "probability": _number(raw.get("probability")) or 0.0,
            "threshold": _number(raw.get("threshold")),
            "alert": bool(raw.get("alert", False)),
            "state": state if state in STATES else None,
            "flare_detected": bool(raw.get("flare_detected", False)),
            "event_id": raw.get("event_id") if isinstance(raw.get("event_id"), int) else None,
            "event_t_start": _iso(raw.get("event_t_start")),
            "location": _location(raw.get("location")),
        },
        "quality": {
            "live_status": live_status(raw),
            "input_age_s": _number(raw.get("input_age_s")),
            "ensemble_std": _number(raw.get("ensemble_std")),
        },
        "provenance": {
            "producer": f"flare-nrt@{version}" if version else "flare-nrt",
            "inputs": _inputs(raw.get("inputs")),
        },
    }
