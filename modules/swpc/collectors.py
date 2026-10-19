"""Collectors: fetch -> adapt -> publish. `manage.py collect <product_id>`
calls these via `COLLECTORS`; systemd timers call the same command
(deploy/swx-collect@.service, docs/ARCHITECTURE.md §6)."""

from __future__ import annotations

import datetime as dt

from django.utils import timezone

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


def collect_goes_xray() -> int:
    raw = client.fetch_json("json/goes/primary/xrays-1-day.json")
    return swxkit.publish_timeseries("noaa.goes.xray", adapters.parse_goes_xray(raw))


def collect_goes_protons() -> int:
    raw = client.fetch_json("json/goes/primary/integral-protons-1-day.json")
    return swxkit.publish_timeseries("noaa.goes.protons", adapters.parse_goes_protons(raw))


# D-092: the 1-day file normally; the 7-day one when nothing was stored in
# the last 20 h (the first run, or after an outage), so the product starts
# with a week and a gap of up to a week fills itself. publish_timeseries
# skips everything at or before the latest stored time_tag either way.
ELECTRONS_CATCH_UP_AFTER = dt.timedelta(hours=20)


def collect_goes_electrons() -> int:
    recent = swxkit.read_series("noaa.goes.electrons", "flux", timezone.now() - ELECTRONS_CATCH_UP_AFTER)
    days = 1 if recent else 7
    raw = client.fetch_json(f"json/goes/primary/integral-electrons-{days}-day.json")
    return swxkit.publish_timeseries("noaa.goes.electrons", adapters.parse_goes_electrons(raw))


def collect_goes_electron_fluence() -> int:
    raw = client.fetch_json("json/goes/primary/integral-electron-fluence-7-day.json")
    return swxkit.publish_timeseries("noaa.goes.electron_fluence",
                                     adapters.parse_goes_electron_fluence(raw, timezone.now()))


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
    "noaa.goes.xray": collect_goes_xray,
    "noaa.goes.protons": collect_goes_protons,
    "noaa.goes.electrons": collect_goes_electrons,
    "noaa.goes.electron_fluence": collect_goes_electron_fluence,
    "noaa.scales": collect_scales,
}
