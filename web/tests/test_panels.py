"""Regression tests for the panel-stack rebuild (12-HANDOFF-2026-10-15.md
§4, instance 2): the panel-to-product mapping, the series endpoint's gap
markers, and the two small physics helpers (xray_class, kp_severity)."""

import pytest
from django.urls import reverse

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
    [(None, None), (0, "ok"), (3.9, "ok"), (4, "stale"), (4.9, "stale"), (5, "serious"), (6.9, "serious"), (7, "error"), (9, "error")],
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


@pytest.mark.django_db
def test_mag_series_includes_bz_and_source(monkeypatch, client):
    # D-053/12-HANDOFF §4: Bz and the active-L1 source were already
    # collected (modules/swpc/adapters.py parse_rtsw_mag) but never
    # surfaced by the old 3-series-per-chart CHART_SPECS.
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(MAG_MANIFEST))
    swxkit.publish_timeseries(
        "noaa.rtsw.mag",
        [{"time_tag": "2026-10-15T00:00:00Z", "source": "SOLAR1", "bt": 4.2, "bx_gsm": 1.0, "by_gsm": -1.0, "bz_gsm": -3.5}],
    )
    response = client.get(reverse("web:product_series_json", args=["noaa.rtsw.mag"]))
    payload = response.json()
    assert payload["series"]["bz_gsm"] == [-3.5]
    assert payload["source"] == ["SOLAR1"]


@pytest.mark.django_db
def test_kp_series_has_no_false_gap_markers(monkeypatch, client):
    # The bug this guards: noaa.kp's collector polls every 15 min
    # (cadence_seconds=900) for data NOAA itself only updates every 3h, so
    # using the manifest cadence as the gap threshold flagged every single
    # real point as "following a gap" (confirmed against real seeded dev
    # data) -- the threshold must come from the series' own observed
    # spacing instead.
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(KP_MANIFEST))
    rows = [
        {"time_tag": f"2026-10-{10 + i // 8:02d}T{(i % 8) * 3:02d}:00:00Z", "kp": 2.0}
        for i in range(16)
    ]
    swxkit.publish_timeseries("noaa.kp", rows)
    response = client.get(reverse("web:product_series_json", args=["noaa.kp"]) + "?range=7d")
    payload = response.json()
    assert len(payload["time"]) == 16
    assert all(v is not None for v in payload["series"]["kp"])


@pytest.mark.django_db
def test_series_endpoint_breaks_a_real_gap(monkeypatch, client):
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(MAG_MANIFEST))
    # Regular 60s cadence, then a 20-minute hole, then regular again.
    rows = [{"time_tag": f"2026-10-15T00:{i:02d}:00Z", "bt": 4.0, "bx_gsm": 0, "by_gsm": 0, "bz_gsm": 0} for i in range(5)]
    rows += [{"time_tag": f"2026-10-15T00:{i:02d}:00Z", "bt": 4.5, "bx_gsm": 0, "by_gsm": 0, "bz_gsm": 0} for i in range(25, 30)]
    swxkit.publish_timeseries("noaa.rtsw.mag", rows)
    response = client.get(reverse("web:product_series_json", args=["noaa.rtsw.mag"]))
    payload = response.json()
    assert len(payload["time"]) == 11  # 5 + 1 synthetic null + 5
    nulls = [i for i, v in enumerate(payload["series"]["bt"]) if v is None]
    assert nulls == [5]


@pytest.mark.django_db
def test_home_panels_cover_every_product_in_order(client):
    response = client.get(reverse("web:home"))
    panels = response.context["panels"]
    product_ids = [p["product_id"] for p in panels]
    assert product_ids == [
        "noaa.rtsw.mag", "noaa.rtsw.mag",
        "noaa.rtsw.wind", "noaa.rtsw.wind", "noaa.rtsw.wind",
        "noaa.kp", "noaa.dst", "noaa.goes.xray", "noaa.goes.protons",
    ]
    bz_panel = next(p for p in panels if p["key"] == "bz")
    assert bz_panel["zero_line"] is True
    assert {s["field"] for s in bz_panel["series"]} == {"bt", "bz_gsm"}

    kp_panel = next(p for p in panels if p["key"] == "kp")
    assert kp_panel["kind"] == "bars"
    assert kp_panel["kp_bucket"] is True


@pytest.mark.django_db
def test_product_detail_panels_scoped_to_that_product(monkeypatch, client):
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(MAG_MANIFEST))
    swxkit.publish_timeseries("noaa.rtsw.mag", [{"time_tag": "2026-10-15T00:00:00Z", "bt": 4.0}])
    response = client.get(reverse("web:product_detail", args=["noaa.rtsw.mag"]))
    panels = response.context["panels"]
    assert [p["key"] for p in panels] == ["bz", "bxby"]
    assert response.context["default_range"] == "3d"
