"""Regression tests for the v0.1.1 hotfix (10-STATUS-AND-NEXT.md §2-§3).

The underlying bug (multi-line {# #} Django comments printed as literal
page text, swallowing the real uPlot <script> tag) passed every existing
test because those only checked response.status_code == 200, never the
rendered HTML. These tests render real pages and inspect the markup.
"""

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone, translation

from core import swxkit
from core.models import Heartbeat, Product


@pytest.fixture(autouse=True)
def _reset_active_language():
    # Django's active-language thread-local survives across test functions
    # in the same process; without resetting it, a test that requested
    # /en/... (like the lang_prefix-parametrized test below) leaks "en" as
    # the active language into whatever test runs next, which silently
    # changes what reverse() returns there too. Caught by this suite
    # itself: two tests failed from cross-test pollution before this fix.
    translation.deactivate_all()
    yield
    translation.deactivate_all()

KP_MANIFEST = {
    "id": "noaa.kp",
    "version": 1,
    "kind": "index",
    "title": {"es": "Índice planetario Kp", "en": "Planetary Kp index"},
    "maturity": "operational",
    "cadence": "15m",
    "stale_after": "6h",
    "producer": {"type": "collector"},
    "source": {"provider": "NOAA SWPC", "license": "Public domain (NOAA)"},
    "parameters": [{"name": "kp", "units": "1"}],
    "exchange": {"hapi": True, "feed": False},
    "_module": "swpc",
    "_path": "modules/swpc/manifests/kp.yaml",
}

FLARE_MANIFEST = {
    "id": "usach.flare.nrt",
    "version": 1,
    "kind": "event",
    "title": {"es": "Detección de fulguraciones (NRT)", "en": "Flare detection (NRT)"},
    "maturity": "beta",
    "cadence": "1m",
    "stale_after": "10m",
    "producer": {"type": "service", "repo": "HelioUSACH/flare-nrt"},
    "source": {"provider": "HelioUSACH (USACH)", "license": "CC-BY-4.0"},
    "parameters": [{"name": "probability", "units": "1"}],
    "exchange": {"hapi": False, "feed": True},
    "_module": "flare",
    "_path": "modules/flare/manifests/nrt.yaml",
}

SCALES_MANIFEST = {
    "id": "noaa.scales",
    "version": 1,
    "kind": "event",
    "title": {"es": "Escalas NOAA (R/S/G)", "en": "NOAA scales (R/S/G)"},
    "maturity": "operational",
    "cadence": "15m",
    "stale_after": "1h",
    "producer": {"type": "collector"},
    "source": {"provider": "NOAA SWPC", "license": "Public domain (NOAA)"},
    "parameters": [{"name": "days", "units": "1"}],
    "exchange": {"hapi": False, "feed": True},
    "_module": "swpc",
    "_path": "modules/swpc/manifests/scales.yaml",
}

PUBLIC_PAGES = [
    "web:home",
    "web:catalogue",
    "web:status",
    "web:que_ofrecemos",
    "web:about",
    "web:data_api",
]


def _manifest_for(product_id):
    return {
        "noaa.kp": KP_MANIFEST,
        "usach.flare.nrt": FLARE_MANIFEST,
        "noaa.scales": SCALES_MANIFEST,
    }[product_id]


@pytest.fixture
def one_product(monkeypatch):
    monkeypatch.setattr(swxkit, "get_manifest", _manifest_for)
    swxkit.publish_timeseries("noaa.kp", [{"time_tag": "2026-10-15T00:00:00Z", "kp": 2.0}])


@pytest.fixture
def client_localhost():
    # product_series_json and friends need Host to be in ALLOWED_HOSTS.
    return Client(SERVER_NAME="localhost")


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", PUBLIC_PAGES)
@pytest.mark.parametrize("lang_prefix", ["", "/en"])
def test_no_raw_template_comment_leaks(client_localhost, one_product, url_name, lang_prefix):
    path = lang_prefix + reverse(url_name)
    response = client_localhost.get(path)
    body = response.content.decode()
    assert "{#" not in body, f"{path}: a Django {{# #}} comment leaked into the rendered HTML"
    assert "#}" not in body, f"{path}: a Django {{# #}} comment leaked into the rendered HTML"
    assert "{% " not in body, f"{path}: a raw template tag leaked into the rendered HTML"


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", PUBLIC_PAGES)
def test_uplot_loads_and_no_external_script_src(client_localhost, one_product, url_name):
    response = client_localhost.get(reverse(url_name))
    body = response.content.decode()
    assert "vendor/uplot/uPlot.iife.min.js" in body, "uPlot's own script tag is missing or was swallowed"
    import re

    external_scripts = re.findall(r'<script[^>]*\ssrc="(https?://[^"]*)"', body)
    assert external_scripts == [], f"found external <script src>: {external_scripts} (D-002: vendored only, no CDN)"


@pytest.mark.django_db
def test_page_titles_use_portal_name_not_byline(client_localhost, one_product):
    response = client_localhost.get(reverse("web:home"))
    body = response.content.decode()
    assert "<title>" in body
    title = body.split("<title>", 1)[1].split("</title>", 1)[0]
    assert "Clima Espacial USACH" in title
    assert "HelioUSACH" not in title


@pytest.mark.django_db
def test_maturity_label_is_translated_on_es_pages(client_localhost, one_product):
    for path in [reverse("web:catalogue"), reverse("web:product_detail", args=["noaa.kp"])]:
        body = client_localhost.get(path).content.decode()
        assert "operacional" in body
        assert ">operational<" not in body


@pytest.mark.django_db
def test_flare_status_reads_pending_not_error(client_localhost, one_product):
    flare_product = Product.objects.create(
        id="usach.flare.nrt",
        kind="event",
        title_es="Detección de fulguraciones (NRT)",
        title_en="Flare detection (NRT)",
        maturity="beta",
        cadence_seconds=60,
        stale_after_seconds=600,
        producer_type="service",
        producer_repo="HelioUSACH/flare-nrt",
        source_provider="HelioUSACH (USACH)",
        manifest_path="modules/flare/manifests/nrt.yaml",
        module="flare",
    )
    Heartbeat.objects.create(
        product=flare_product,
        status="error",
        last_seen_at=timezone.now(),
        detail="flare-nrt status endpoint unreachable",
    )
    # Neither home.html (flare is outside context_product_ids) nor
    # catalogue.html (flare is excluded from the generic grid — its own
    # dedicated offer card covers it, see the dedup test below) show the
    # generic per-product status badge for flare; /estado/ does.
    body = client_localhost.get(reverse("web:status")).content.decode()
    assert "en preparación" in body
    assert "con errores" not in body


@pytest.mark.django_db
def test_flare_product_not_duplicated_on_catalogue(client_localhost, one_product):
    # Found while verifying the Q5 fix: once a real usach.flare.nrt Product
    # row exists (true in production as soon as modules/flare's collector
    # has run once), it showed up twice on /productos/ -- the dedicated
    # "beta" offer card at the top, and again in the generic product grid.
    Product.objects.create(
        id="usach.flare.nrt",
        kind="event",
        title_es="Detección de fulguraciones (NRT)",
        title_en="Flare detection (NRT)",
        maturity="beta",
        cadence_seconds=60,
        stale_after_seconds=600,
        producer_type="service",
        producer_repo="HelioUSACH/flare-nrt",
        source_provider="HelioUSACH (USACH)",
        manifest_path="modules/flare/manifests/nrt.yaml",
        module="flare",
    )
    body = client_localhost.get(reverse("web:catalogue")).content.decode()
    assert body.count("Detección de fulguraciones") == 1


@pytest.mark.django_db
def test_status_page_never_shows_raw_collector_detail_text(client_localhost, one_product):
    hb = Heartbeat.objects.get(product_id="noaa.kp")
    hb.detail = "published 3 new point(s) of 5 fetched"
    hb.save(update_fields=["detail"])
    body = client_localhost.get(reverse("web:status")).content.decode()
    assert "published" not in body
    assert "point(s)" not in body


@pytest.mark.django_db
def test_scale_badge_text_is_translated(client_localhost, monkeypatch):
    monkeypatch.setattr(swxkit, "get_manifest", _manifest_for)
    swxkit.publish_event(
        "noaa.scales",
        {"days": {"0": {"DateStamp": "2026-10-15", "TimeStamp": "00:00:00",
                         "R": {"Scale": "0", "Text": "none"},
                         "S": {"Scale": "0", "Text": "none"},
                         "G": {"Scale": "0", "Text": "none"}}}},
        valid_from="2026-10-15T00:00:00Z",
        issued="2026-10-15T00:00:00Z",
    )
    body = client_localhost.get(reverse("web:home")).content.decode()
    assert "ninguna" in body
    assert ">none<" not in body
