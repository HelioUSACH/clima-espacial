"""v0.1.3 public-facing fixes (13-AUDIT §3), checked on the rendered HTML in
both languages, plus hashed static files (D-055) checked against a real
`collectstatic` run with the production storage."""

import datetime as dt
import re

import pytest
from django.core.management import call_command
from django.templatetags.static import static
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone, translation

from core import swxkit

from .test_panels import MAG_MANIFEST

DEVELOPER_TEXT = (
    "INTEGRATION.md",
    "(M2)",
    "registro de interés para M1",
    "interest form for M1",
    "una vez publicado el endpoint",
    "Franja de contexto",
    "Context strip",
)


@pytest.fixture
def mag_product(monkeypatch):
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(MAG_MANIFEST))
    swxkit.publish_timeseries("noaa.rtsw.mag", [{"time_tag": timezone.now() - dt.timedelta(minutes=5), "bt": 4.0}])


def _get(client, url_name, lang, *args):
    with translation.override(lang):  # reverse() adds the /en/ prefix itself
        url = reverse(url_name, args=args)
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("lang", ["es", "en"])
@pytest.mark.parametrize("url_name", ["web:home", "web:data_api", "web:product_detail"])
def test_no_developer_text_on_public_pages(client, mag_product, url_name, lang):
    args = ("noaa.rtsw.mag",) if url_name == "web:product_detail" else ()
    html = _get(client, url_name, lang, *args)
    for text in DEVELOPER_TEXT:
        assert text not in html, f"{text!r} leaked onto {url_name} ({lang})"


@pytest.mark.django_db
@pytest.mark.parametrize("lang, heading", [("es", "Viento solar en L1"), ("en", "Solar wind in L1")])
def test_home_context_heading(client, lang, heading):
    # 18-HANDOFF-v0.2.0.md §2: home's only chart section now, heading taken
    # straight from the approved mockup (supersedes 13-AUDIT §3's heading).
    assert f'<h2 id="l1-h">{heading}</h2>' in _get(client, "web:home", lang)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "lang, licence, dialog_title",
    [("es", "Dominio público (NOAA)", "Descargar CSV"), ("en", "Public domain (NOAA)", "Download CSV")],
)
def test_product_page_licence_and_csv_button(client, mag_product, lang, licence, dialog_title):
    # 18-HANDOFF-v0.2.0.md §2: the licence lives in "Ficha del producto".
    # v0.5.0 §3: the disabled "CSV · próximamente" stub became a working
    # CSV button that opens the download dialog.
    html = _get(client, "web:product_detail", lang, "noaa.rtsw.mag")
    assert f"<dd>{licence}</dd>" in html
    assert re.search(r'<button[^>]*data-csv-open="csv-dialog"[^>]*>CSV</button>', html)
    assert f'<h2 id="csv-dialog-title">{dialog_title}</h2>' in html
    assert "próximamente" not in html and "coming soon" not in html
    if lang == "es":
        assert "Public domain" not in html


@pytest.mark.django_db
def test_chart_i18n_carries_ut_label_and_language(client):
    with translation.override("en"):
        response = client.get(reverse("web:home"))
    i18n = response.context["chart_i18n"]
    assert i18n["lang"] == "en" and i18n["time_axis_label"] == "UT"
    assert i18n["months"][9] == "Oct"
    with translation.override("es"):
        assert client.get(reverse("web:home")).context["chart_i18n"]["lang"] == "es"


XRAY_MANIFEST = {
    "id": "noaa.goes.xray", "version": 1, "kind": "timeseries",
    "title": {"es": "Flujo de rayos X (GOES)", "en": "X-ray flux (GOES)"},
    "maturity": "operational", "cadence": "5m", "stale_after": "15m",
    "producer": {"type": "collector"}, "source": {"provider": "NOAA SWPC", "license": "Public domain (NOAA)"},
    "parameters": [{"name": "flux", "units": "W/m2"}],
    "exchange": {"hapi": True, "feed": False},
    "_module": "swpc", "_path": "modules/swpc/manifests/goes_xray.yaml",
}


@pytest.mark.django_db
def test_xray_panel_has_fixed_axis_and_envelope_flags(client, monkeypatch):
    # The X-ray panel lives on its own product page now (18-HANDOFF §2:
    # removed from home), so the fixed-axis/class-band checks move there.
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(XRAY_MANIFEST))
    swxkit.publish_timeseries(
        "noaa.goes.xray",
        [{"time_tag": timezone.now() - dt.timedelta(minutes=5), "flux": 1e-7, "energy": "0.1-0.8nm"}],
    )
    panels = {p["key"]: p for p in client.get(reverse("web:product_detail", args=["noaa.goes.xray"])).context["panels"]}
    assert panels["xray"]["y_range"] == [1e-9, 1e-2]
    assert [b["label"] for b in panels["xray"]["log_bands"]] == ["A", "B", "C", "M", "X"]

    home_panels = {p["key"]: p for p in client.get(reverse("web:home")).context["panels"]}
    # The min-max band is for the solar-wind fields only, all of which
    # still live on home.
    assert {k for k, p in home_panels.items() if p["envelope"]} == {"bz", "bxby", "speed", "density", "temperature"}


def test_production_settings_use_hashed_static_files():
    from climaespacial.settings import base

    assert base.STORAGES["staticfiles"]["BACKEND"].endswith("ManifestStaticFilesStorage")


@pytest.mark.django_db
def test_collectstatic_with_manifest_storage_hashes_every_page_asset(tmp_path, client):
    # The real thing deploy.sh runs: collectstatic with the production
    # storage, then a page rendered against that manifest must reference
    # only hashed files that actually exist in STATIC_ROOT.
    storages = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
    }
    with override_settings(STORAGES=storages, STATIC_ROOT=tmp_path):
        call_command("collectstatic", interactive=False, verbosity=0)
        assert (tmp_path / "staticfiles.json").exists()
        assert re.fullmatch(r"/?static/web/js/charts\.[0-9a-f]{12}\.js", static("web/js/charts.js"))
        html = client.get(reverse("web:home")).content.decode()
    assets = re.findall(r'(?:src|href)="/?static/([^"]+)"', html)
    assert len(assets) >= 5
    for asset in assets:
        # Identity v1 (18-HANDOFF §2) added self-hosted font preloads, v0.3.0
        # the group emblem -- ManifestStaticFilesStorage hashes those too.
        assert re.search(r"\.[0-9a-f]{12}\.(js|css|woff2|png)$", asset), asset
        assert (tmp_path / asset).exists(), asset
    # The hero background is a url() inside base.css: the hashed stylesheet
    # must point at the hashed illustration, or the hero band paints bare.
    css = next(a for a in assets if a.startswith("web/css/base."))
    bg = re.findall(r'url\("?(\.\./img/solar-wind-magnetosphere\.[0-9a-f]{12}\.svg)', (tmp_path / css).read_text())
    assert bg, "base.css does not reference a hashed hero illustration"
    assert (tmp_path / "web/css" / bg[0]).resolve().exists()
