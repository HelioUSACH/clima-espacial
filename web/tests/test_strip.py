"""The header status strip (18-HANDOFF-v0.2.0.md §2, §3): R/S/G, Kp and Dst
chips rendered on *every* page via web.context_processors.strip, with the
right severity CSS class for a given value -- including the two sample
values the quality bar names explicitly (Kp = 5, Dst = -120)."""

import pytest
from django.core.cache import cache
from django.urls import reverse

from core import swxkit

SCALES_MANIFEST = {
    "id": "noaa.scales", "version": 1, "kind": "event",
    "title": {"es": "Escalas NOAA (R/S/G)", "en": "NOAA scales (R/S/G)"},
    "maturity": "operational", "cadence": "15m", "stale_after": "1h",
    "producer": {"type": "collector"}, "source": {"provider": "NOAA SWPC", "license": "Public domain (NOAA)"},
    "parameters": [{"name": "days", "units": "1"}],
    "exchange": {"hapi": False, "feed": True},
    "_module": "swpc", "_path": "modules/swpc/manifests/scales.yaml",
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

DST_MANIFEST = {
    "id": "noaa.dst", "version": 1, "kind": "index",
    "title": {"es": "Índice Dst (Kyoto)", "en": "Dst index (Kyoto)"},
    "maturity": "operational", "cadence": "15m", "stale_after": "3h",
    "producer": {"type": "collector"}, "source": {"provider": "NOAA SWPC (Kyoto WDC)"},
    "parameters": [{"name": "dst", "units": "nT"}],
    "exchange": {"hapi": True, "feed": False},
    "_module": "swpc", "_path": "modules/swpc/manifests/dst.yaml",
}


@pytest.fixture(autouse=True)
def _clear_strip_cache():
    # web.context_processors._strip_raw() caches for 30s (own cache key,
    # unrelated to conftest's blanket cache.clear(), kept explicit here so
    # this file's intent reads standalone).
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
@pytest.mark.parametrize("kp, expected_class", [(5, "sv-minor"), (6, "sv-moderate"), (4.9, "sv-ok"), (7, "sv-strong"), (8, "sv-severe")])
def test_kp_chip_severity_class(monkeypatch, client, kp, expected_class):
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(KP_MANIFEST))
    swxkit.publish_timeseries("noaa.kp", [{"time_tag": "2026-10-16T12:00:00Z", "kp": kp}])
    body = client.get(reverse("web:home")).content.decode()
    assert f'Kp <i class="{expected_class}">' in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    "dst, expected_class",
    [(-120, "sv-strong"), (-30, "sv-ok"), (-75, "sv-moderate"), (-300, "sv-severe")],
)
def test_dst_chip_severity_class(monkeypatch, client, dst, expected_class):
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(DST_MANIFEST))
    swxkit.publish_timeseries("noaa.dst", [{"time_tag": "2026-10-16T12:00:00Z", "dst": dst}])
    body = client.get(reverse("web:home")).content.decode()
    assert f'Dst <i class="{expected_class}">' in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    "level, expected_class",
    [(0, "sv-ok"), (1, "sv-minor"), (2, "sv-moderate"), (3, "sv-strong"), (4, "sv-severe"), (5, "sv-severe")],
)
def test_scale_chip_severity_class(monkeypatch, client, level, expected_class):
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(SCALES_MANIFEST))
    swxkit.publish_event(
        "noaa.scales",
        {"days": {"0": {"DateStamp": "2026-10-16", "TimeStamp": "12:00:00",
                        "G": {"Scale": str(level), "Text": "none"},
                        "R": {"Scale": "0", "Text": "none"}, "S": {"Scale": "0", "Text": "none"}}}},
        valid_from="2026-10-16T12:00:00Z", issued="2026-10-16T12:00:00Z",
    )
    body = client.get(reverse("web:home")).content.decode()
    assert f'Geomagnética <i class="{expected_class}">G{level}</i>' in body


@pytest.mark.django_db
def test_strip_renders_on_a_non_home_page_too(client):
    # It's a context processor (every page), not a home()-only context var.
    body = client.get(reverse("web:about")).content.decode()
    assert '<div class="strip">' in body
    assert 'class="scale"' in body
