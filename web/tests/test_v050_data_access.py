"""v0.5.0 on the public pages: /datos-api/, the product page's CSV button,
the home card, the forecast export links."""

import re
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import translation

from core import swxkit

JS = Path(__file__).resolve().parents[1] / "static" / "web" / "js" / "data-access.js"


def _get(client, name, lang="es", *args):
    with translation.override("es"):
        path = reverse(name, args=args)
    return client.get(("/en" if lang == "en" else "") + path).content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("lang,relay,not_exported,privacy", [
    ("es", "Series de terceros", "Series que mostramos pero no redistribuimos", "huella (hash) de tu llave"),
    ("en", "Third-party series", "Series we show but don't redistribute", "fingerprint (hash) of your API key"),
])
def test_data_access_page(client, lang, relay, not_exported, privacy):
    html = _get(client, "web:data_api", lang)
    for dataset in ("noaa.goes.protons", "noaa.goes.xray", "noaa.kp", "noaa.rtsw.mag", "noaa.rtsw.wind"):
        assert f"<td><code>{dataset}</code></td>" in html
    assert relay in html and not_exported in html and privacy in html
    # Kyoto: listed with its provider link, never as a dataset.
    assert "<code>kyoto." not in html
    assert 'href="https://wdc.kugi.kyoto-u.ac.jp/dst_realtime/index.html"' in html
    assert reverse("api:forecast_json", args=["usach.kp.persistence"]) in html
    assert "http://testserver/hapi" in html and "X-API-Key" in html
    assert 'name="website"' in html  # the honeypot
    for funder_code in ("FONDEF", "FIU", "ANID"):
        assert funder_code not in html


@pytest.mark.django_db
def test_exportable_product_gets_the_csv_dialog(client):
    swxkit.heartbeat("noaa.kp", "ok")
    html = _get(client, "web:product_detail", "es", "noaa.kp")
    assert 'data-csv-open="csv-dialog"' in html
    assert f'data-endpoint="{reverse("api:download_csv", args=["noaa.kp"])}"' in html
    assert "web/js/data-access.js" in html
    assert 'href="/datos-api/"' in html


@pytest.mark.django_db
@pytest.mark.parametrize("lang,label", [("es", "Datos en el sitio del proveedor"),
                                        ("en", "Data on the provider's site")])
def test_non_exportable_product_links_the_provider_instead(client, lang, label):
    swxkit.publish_timeseries("kyoto.dst", [{"time_tag": "2026-10-18T00:00:00Z", "dst": -9, "version": 0}])
    html = _get(client, "web:product_detail", lang, "kyoto.dst")
    assert "data-csv-open" not in html and "data-access.js" not in html
    assert re.search(r'<a class="sbtn" href="https://wdc\.kugi\.kyoto-u\.ac\.jp/dst_realtime/index\.html" '
                     r'rel="noopener nofollow">' + re.escape(label), html)


@pytest.mark.django_db
def test_home_data_card_is_available_and_licences_are_right(client):
    html = _get(client, "web:home")
    assert "Series curadas con licencia CC BY 4.0" not in html
    assert "los productos USACH son CC BY 4.0 y los datos de la NOAA, de dominio público" in html
    card = html[html.index("Datos abiertos y API") - 200:html.index("Datos abiertos y API") + 200]
    assert "disponible" in card and "próximamente" not in card


@pytest.mark.django_db
def test_forecast_page_links_its_export(client):
    swxkit.heartbeat("usach.kp.persistence", "ok")
    html = _get(client, "web:product_detail", "es", "usach.kp.persistence")
    assert reverse("api:forecast_csv", args=["usach.kp.persistence"]) in html


def test_every_local_storage_access_is_guarded():
    # Storage can be blocked (private mode, a privacy setting): every access
    # sits inside a try on the same line, so the dialog keeps working.
    lines = [line for line in JS.read_text().splitlines() if "localStorage" in line and not line.strip().startswith("*")]
    assert lines
    assert all("try {" in line for line in lines), lines
