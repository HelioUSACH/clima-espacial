"""Collectors: fetch -> adapt -> publish. `manage.py collect <product_id>`
calls these via `COLLECTORS`; systemd timers call the same command
(deploy/swx-collect@.service, docs/ARCHITECTURE.md §6)."""

from __future__ import annotations

from core import swxkit

from . import adapters, client


def collect_rtsw_mag() -> int:
    raw = client.fetch_json("json/rtsw/rtsw_mag_1m.json")
    return swxkit.publish_timeseries("noaa.rtsw.mag", adapters.parse_rtsw_mag(raw))


def collect_rtsw_wind() -> int:
    raw = client.fetch_json("json/rtsw/rtsw_wind_1m.json")
    return swxkit.publish_timeseries("noaa.rtsw.wind", adapters.parse_rtsw_wind(raw))


def collect_kp() -> int:
    raw = client.fetch_json("products/noaa-planetary-k-index.json")
    return swxkit.publish_timeseries("noaa.kp", adapters.parse_kp(raw))


def collect_dst() -> int:
    raw = client.fetch_json("products/kyoto-dst.json")
    return swxkit.publish_timeseries("noaa.dst", adapters.parse_dst(raw))


def collect_goes_xray() -> int:
    raw = client.fetch_json("json/goes/primary/xrays-1-day.json")
    return swxkit.publish_timeseries("noaa.goes.xray", adapters.parse_goes_xray(raw))


def collect_goes_protons() -> int:
    raw = client.fetch_json("json/goes/primary/integral-protons-1-day.json")
    return swxkit.publish_timeseries("noaa.goes.protons", adapters.parse_goes_protons(raw))


def collect_scales() -> None:
    raw = client.fetch_json("products/noaa-scales.json")
    parsed = adapters.parse_scales(raw)
    swxkit.publish_event(
        "noaa.scales",
        parsed["payload"],
        valid_from=parsed["issued"],
        issued=parsed["issued"],
    )


COLLECTORS = {
    "noaa.rtsw.mag": collect_rtsw_mag,
    "noaa.rtsw.wind": collect_rtsw_wind,
    "noaa.kp": collect_kp,
    "noaa.dst": collect_dst,
    "noaa.goes.xray": collect_goes_xray,
    "noaa.goes.protons": collect_goes_protons,
    "noaa.scales": collect_scales,
}
