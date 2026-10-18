"""D-078: `source.links` in the manifest, on Product, and where the pages
show them; the footer's licence line."""

import copy
import re

import pytest
import yaml
from django.core.management import call_command
from django.urls import reverse
from django.utils import translation

from core import swxkit
from core.manifests import loader
from core.manifests.loader import ManifestError, load_manifest, manifest_warnings
from core.models import Product
from core.tests.test_forecast_manifest import FORECAST

KP_LINKS = [
    {"name": "NOAA SWPC", "url": "https://www.spaceweather.gov/products/planetary-k-index"},
    {"name": "GFZ Potsdam", "url": "https://kp.gfz.de/en/"},
]


@pytest.fixture(autouse=True)
def _reset_active_language():
    translation.deactivate_all()
    yield
    translation.deactivate_all()


def _write(tmp_path, data):
    path = tmp_path / "modules" / "demo" / "manifests" / "product.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))
    return path


def _with_links(links):
    data = copy.deepcopy(FORECAST)
    data["source"]["links"] = links
    return data


def test_schema_accepts_links(tmp_path):
    manifest = load_manifest(_write(tmp_path, _with_links(KP_LINKS)))
    assert manifest["source"]["links"] == KP_LINKS


@pytest.mark.parametrize("links, fragment", [
    ([{"name": "NOAA", "url": "http://www.swpc.noaa.gov/"}], "does not match"),
    ([{"name": "NOAA"}], "'url' is a required property"),
    ([{"name": "", "url": "https://x.org/"}], "should be non-empty"),
    ([{"name": "NOAA", "url": "https://x.org/", "lang": "en"}], "Additional properties are not allowed"),
])
def test_schema_rejects_bad_links(tmp_path, links, fragment):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, _with_links(links)))
    assert any(fragment in e for e in exc.value.errors), exc.value.errors


def test_warning_for_a_third_party_product_without_links():
    assert manifest_warnings({"source": {"provider": "NOAA SWPC"}})
    assert manifest_warnings({"source": {"provider": "NOAA SWPC", "links": KP_LINKS}}) == []
    assert manifest_warnings({"source": {"provider": "HelioUSACH (USACH)"}}) == []


def test_validate_manifests_warns_but_passes(tmp_path, monkeypatch, capsys):
    data = copy.deepcopy(FORECAST)
    data["source"]["provider"] = "Someone else"
    data["forecast"]["target"] = {"product": "usach.test.kpmodel", "field": "kp"}
    data["kind"] = "timeseries"
    del data["forecast"], data["model_card"]
    data["parameters"] = [{"name": "kp"}]
    _write(tmp_path, data)
    monkeypatch.setattr(loader, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("core.management.commands.validate_manifests.discover_manifest_paths",
                        lambda: loader.discover_manifest_paths(tmp_path))
    call_command("validate_manifests")
    assert "source.links is empty" in capsys.readouterr().out


def test_every_third_party_manifest_in_the_repo_has_links():
    for manifest in loader.discover_manifests().values():
        assert manifest_warnings(manifest) == [], manifest["id"]


@pytest.mark.django_db
def test_links_are_synced_onto_the_product():
    swxkit.heartbeat("noaa.kp", "ok")
    assert Product.objects.get(id="noaa.kp").source_links == KP_LINKS
    swxkit.heartbeat("usach.kp.persistence", "ok")
    assert Product.objects.get(id="usach.kp.persistence").source_links == []


@pytest.mark.django_db
def test_product_page_links_the_provider_pill_and_lists_every_link(client):
    swxkit.heartbeat("noaa.kp", "ok")
    html = client.get(reverse("web:product_detail", args=["noaa.kp"])).content.decode()
    pill = re.search(r'<a class="pill" href="([^"]+)" rel="noopener nofollow">NOAA SWPC <svg', html)
    assert pill and pill.group(1) == KP_LINKS[0]["url"]
    row = html[html.index("En el sitio del proveedor"):]
    row = row[:row.index("</dd>")]
    for link in KP_LINKS:
        assert f'href="{link["url"]}"' in row and f'>{link["name"]} <svg' in row


@pytest.mark.django_db
def test_product_page_without_links_keeps_a_plain_pill(client):
    swxkit.heartbeat("noaa.kp", "ok")
    Product.objects.filter(id="noaa.kp").update(source_links=[])
    html = client.get(reverse("web:product_detail", args=["noaa.kp"])).content.decode()
    assert '<span class="pill">NOAA SWPC</span>' in html
    assert "En el sitio del proveedor" not in html


@pytest.mark.django_db
def test_event_page_links_source_and_official_alerts(client):
    swxkit.heartbeat("noaa.scales", "ok")
    html = client.get(reverse("web:product_detail", args=["noaa.scales"])).content.decode()
    assert 'Fuente: <a href="https://www.spaceweather.gov/noaa-scales-explanation"' in html
    assert 'Avisos oficiales: <a href="https://www.spaceweather.gov/products/alerts-watches-and-warnings"' in html


@pytest.mark.django_db
def test_forecast_note_links_the_targets_provider(client):
    swxkit.heartbeat("usach.kp.persistence", "no_data")
    html = client.get(reverse("web:product_detail", args=["usach.kp.persistence"])).content.decode()
    note = html[html.index("Verificado contra"):]
    note = note[:note.index("</p>")]
    assert f'(<a href="{KP_LINKS[0]["url"]}" rel="noopener nofollow">NOAA SWPC <svg' in note


@pytest.mark.django_db
def test_catalogue_card_keeps_exactly_one_link(client):
    swxkit.heartbeat("noaa.kp", "ok")
    html = client.get(reverse("web:catalogue")).content.decode()
    card = next(c for c in html.split('<div class="product-card">')[1:] if "productos/noaa.kp/" in c)
    card = card[:card.index("</div>")]
    assert card.count("<a ") == 1


@pytest.mark.django_db
@pytest.mark.parametrize("lang, licence, sources", [
    ("es", "Código Apache-2.0 · productos USACH CC BY 4.0 · datos de terceros según su proveedor", "Fuentes de datos"),
    ("en", "Code Apache-2.0 · USACH products CC BY 4.0 · third-party data under their provider's terms",
     "Data sources"),
])
def test_footer_licence_and_data_sources(client, lang, licence, sources):
    with translation.override(lang):
        url = reverse("web:catalogue")
    html = client.get(url).content.decode()
    footer = html[html.index('<footer class="foot"'):]
    assert licence in footer and "datos CC BY 4.0</p>" not in footer
    assert sources in footer
    for url in ("https://www.spaceweather.gov/", "https://wdc.kugi.kyoto-u.ac.jp/dst_realtime/index.html",
                "https://kp.gfz.de/en/"):
        assert f'href="{url}"' in footer
