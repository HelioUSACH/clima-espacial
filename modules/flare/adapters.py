"""Parses flare-nrt's `/api/status` response (shape read directly from
`HelioUSACH/flare-nrt`'s `nrt_server_v3.py::api_status`, 2026-10 — re-sync
this adapter and its fixture if that endpoint's shape changes, same as any
other external adapter in this repo).

Deliberately **not** surfaced in the public event payload: `severity`
(01-AUDIT.md §3.6 — self-reports accuracy 0.39, "exploratory_low"; showing
it as if it were a usable signal would be dishonest, D-036/D-044 "honest
science"). It stays available to the flare-nrt service's own expert
dashboard, just not republished here."""

from __future__ import annotations


def parse_status(raw: dict) -> dict | None:
    """Returns None when the service hasn't produced a result yet (fresh
    start, `last_update` still null) — the collector then skips publishing
    rather than inventing a zero-confidence event."""
    if not raw.get("timestamp"):
        return None
    return {
        "issued": raw["timestamp"],
        "valid_from": raw["timestamp"],
        "payload": {
            "probability": raw.get("probability", 0.0),
            "flare_detected": bool(raw.get("flare_detected", False)),
            "location": raw.get("location"),
        },
        "quality": {
            "producer_live_status": raw.get("live_status"),  # "live" | "stale" | "no_data" | "unknown"
            "ensemble_std": raw.get("ensemble_std"),
        },
    }
