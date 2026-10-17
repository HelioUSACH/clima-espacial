"""Regression tests for the panel-stack rebuild (12-HANDOFF-2026-10-15.md
§4, instance 2): the panel-to-product mapping, the series endpoint's gaps,
and the two small physics helpers (xray_class, kp_severity). The bucketing
contract itself (D-056) is covered in test_series.py."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from core import swxkit
from web import charts


@pytest.mark.parametrize(
    "flux, expected",
    [
        (None, None),
        (0, None),
        (9.2e-7, "B9.2"),
        (1.5e-6, "C1.5"),
        (2.0e-5, "M2.0"),
        (3.1e-4, "X3.1"),
        (5e-9, "A0.5"),
    ],
)
def test_xray_class_breakpoints(flux, expected):
    assert charts.xray_class(flux) == expected


@pytest.mark.parametrize(
    "kp, expected",
    # 18-HANDOFF-v0.2.0.md §2: "Kp < 5 -> ok, 5 -> minor, 6 -> moderate,
    # 7 -> strong, >= 8 -> severe" (supersedes D-053's 4-step scale).
    [(None, None), (0, "ok"), (4.9, "ok"), (5, "minor"), (5.9, "minor"), (6, "moderate"), (7, "strong"), (8, "severe"), (9, "severe")],
)
def test_kp_severity_buckets(kp, expected):
    assert charts.kp_severity(kp) == expected


MAG_MANIFEST = {
    "id": "noaa.rtsw.mag", "version": 1, "kind": "timeseries",
    "title": {"es": "Campo magnético interplanetario (L1)", "en": "Interplanetary magnetic field (L1)"},
    "maturity": "operational", "cadence": "1m", "stale_after": "10m",
    "producer": {"type": "collector"}, "source": {"provider": "NOAA SWPC", "license": "Public domain (NOAA)"},
    "parameters": [{"name": "bt", "units": "nT"}],
    "exchange": {"hapi": True, "feed": False},
    "_module": "swpc", "_path": "modules/swpc/manifests/rtsw_mag.yaml",
    # 18-HANDOFF-v0.2.0.md §2: the product page's "Qué muestra"/"Ficha del
    # producto" read these from the Product row, synced from exactly this
    # shape (core/swxkit.py _sync_product).
    "description": {"es": "Descripción de prueba.", "en": "Test description."},
    "facts": {"license": {"es": "Dominio público (NOAA)", "en": "Public domain (NOAA)"}},
}

KP_MANIFEST = {
    "id": "noaa.kp", "version": 1, "kind": "index",
    "title": {"es": "Índice planetario Kp", "en": "Planetary Kp index"},
    "maturity": "operational", "cadence": "15m", "stale_after": "6h",
    "producer": {"type": "collector"}, "source": {"provider": "NOAA SWPC", "license": "Public domain (NOAA)"},
    "parameters": [{"name": "kp", "units": "1"}],
    "exchange": {"hapi": True, "feed": False},
    "_module": "swpc", "_path": "modules/swpc/manifests/kp.yaml",
}


def _minute(minutes_ago):
    # Relative to now and on a whole minute (the 6 h range keeps raw 1-min
    # buckets), so these tests don't age out of the requested range.
    return timezone.now().replace(second=0, microsecond=0) - dt.timedelta(minutes=minutes_ago)


@pytest.mark.django_db
def test_mag_series_includes_bz_and_source(monkeypatch, client):
    # D-053/12-HANDOFF §4: Bz and the active-L1 source were already
    # collected (modules/swpc/adapters.py parse_rtsw_mag) but never
    # surfaced by the old 3-series-per-chart CHART_SPECS.
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(MAG_MANIFEST))
    swxkit.publish_timeseries(
        "noaa.rtsw.mag",
        [{"time_tag": _minute(30), "source": "SOLAR1", "bt": 4.2, "bx_gsm": 1.0, "by_gsm": -1.0, "bz_gsm": -3.5}],
    )
    response = client.get(reverse("web:product_series_json", args=["noaa.rtsw.mag"]) + "?range=6h")
    payload = response.json()
    assert payload["series"]["bz_gsm"] == [-3.5]
    assert payload["source"] == ["SOLAR1"]


@pytest.mark.django_db
@pytest.mark.parametrize("range_key", ["1d", "3d", "7d"])
def test_kp_series_has_no_false_gap_markers(monkeypatch, client, range_key):
    # The bug this guards: noaa.kp's collector polls every 15 min
    # (cadence_seconds=900) for data NOAA itself only updates every 3h, so
    # using the manifest cadence as the gap threshold flagged every single
    # real point as "following a gap" (confirmed against real seeded dev
    # data) -- the gap step must come from the series' own observed
    # spacing instead. Still true with buckets (2-15 min) much shorter
    # than the 3 h spacing (D-056).
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(KP_MANIFEST))
    now = timezone.now()
    last = now.replace(hour=now.hour - now.hour % 3, minute=0, second=0, microsecond=0)
    n = int(charts.RANGE_DAYS[range_key] * 8) - 1  # 3-hourly points that fit inside the range
    rows = [{"time_tag": last - dt.timedelta(hours=3 * i), "kp": 2.0} for i in reversed(range(n))]
    swxkit.publish_timeseries("noaa.kp", rows)
    response = client.get(reverse("web:product_series_json", args=["noaa.kp"]) + f"?range={range_key}")
    payload = response.json()
    assert len(payload["time"]) == n
    assert all(v is not None for v in payload["series"]["kp"])


@pytest.mark.django_db
def test_series_endpoint_breaks_a_real_gap(monkeypatch, client):
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(MAG_MANIFEST))
    # Regular 60s cadence, then a 20-minute hole, then regular again.
    rows = [{"time_tag": _minute(60 - i), "bt": 4.0, "bx_gsm": 0, "by_gsm": 0, "bz_gsm": 0} for i in range(5)]
    rows += [{"time_tag": _minute(60 - i), "bt": 4.5, "bx_gsm": 0, "by_gsm": 0, "bz_gsm": 0} for i in range(25, 30)]
    swxkit.publish_timeseries("noaa.rtsw.mag", rows)
    response = client.get(reverse("web:product_series_json", args=["noaa.rtsw.mag"]) + "?range=6h")
    payload = response.json()
    # 1-min buckets at 6 h: every missing minute is an explicit null bucket
    # (D-056), so the line breaks across the hole and nowhere else.
    assert len(payload["time"]) == 30
    nulls = [i for i, v in enumerate(payload["series"]["bt"]) if v is None]
    assert nulls == list(range(5, 25))
    assert payload["series"]["bt"][:5] == [4.0] * 5 and payload["series"]["bt"][25:] == [4.5] * 5


@pytest.mark.django_db
def test_home_panels_cover_every_product_in_order(client):
    # 18-HANDOFF-v0.2.0.md §2: "Removed from home: the Kp, Dst, X-ray and
    # proton panels and the v0.1.2 'now' row" -- only the solar-wind stack
    # (mag + wind) remains; those four products' panels moved to their own
    # product pages (see test_xray_panel_has_fixed_axis_and_envelope_flags).
    response = client.get(reverse("web:home"))
    panels = response.context["panels"]
    product_ids = [p["product_id"] for p in panels]
    assert product_ids == [
        "noaa.rtsw.mag", "noaa.rtsw.mag",
        "noaa.rtsw.wind", "noaa.rtsw.wind", "noaa.rtsw.wind",
    ]
    bz_panel = next(p for p in panels if p["key"] == "bz")
    assert bz_panel["zero_line"] is True
    assert {s["field"] for s in bz_panel["series"]} == {"bt", "bz_gsm"}


@pytest.mark.django_db
def test_kp_panel_lives_on_its_own_product_page(monkeypatch, client):
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(KP_MANIFEST))
    swxkit.publish_timeseries("noaa.kp", [{"time_tag": _minute(5), "kp": 2.0}])
    response = client.get(reverse("web:product_detail", args=["noaa.kp"]))
    panels = response.context["panels"]
    kp_panel = next(p for p in panels if p["key"] == "kp")
    assert kp_panel["kind"] == "bars"
    assert kp_panel["kp_bucket"] is True


@pytest.mark.django_db
def test_product_detail_panels_scoped_to_that_product(monkeypatch, client):
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(MAG_MANIFEST))
    # A week of history, so every range button is on (see test_series.py
    # for the fallback when the history is shorter than the default range).
    swxkit.publish_timeseries(
        "noaa.rtsw.mag", [{"time_tag": _minute(7 * 1440 + 10), "bt": 4.0}, {"time_tag": _minute(5), "bt": 4.0}]
    )
    response = client.get(reverse("web:product_detail", args=["noaa.rtsw.mag"]))
    panels = response.context["panels"]
    assert [p["key"] for p in panels] == ["bz", "bxby"]
    assert response.context["default_range"] == "3d"
