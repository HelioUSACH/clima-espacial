"""v0.5.0 §0: "al día · con retraso" (D-085), the headline's "as of" time,
and DOI links in "Ficha del producto"."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone, translation

from core import swxkit
from core.models import Heartbeat, Product
from web.product_status import public_status
from web.views import link_dois


def _url(name, *args, lang="es"):
    # reverse() follows the active language, which a previous /en/ request
    # leaves behind: build the unprefixed path, then add the prefix.
    with translation.override("es"):
        path = reverse(name, args=args)
    return ("/en" if lang == "en" else "") + path


def _ae_rows(end, n=10):
    return [{"time_tag": end - dt.timedelta(minutes=i), "ae": 218 + i, "al": -150, "au": 68, "ao": -41, "version": 0}
            for i in range(n)]


@pytest.mark.django_db
def test_delayed_after_is_synced_from_the_manifest():
    swxkit.heartbeat("kyoto.ae", "ok")
    assert Product.objects.get(id="kyoto.ae").delayed_after_seconds == 2 * 86400
    swxkit.heartbeat("noaa.kp", "ok")
    assert Product.objects.get(id="noaa.kp").delayed_after_seconds is None


@pytest.mark.django_db
def test_healthy_and_recent_is_ok():
    swxkit.publish_timeseries("kyoto.ae", _ae_rows(timezone.now() - dt.timedelta(hours=3)))
    product = Product.objects.get(id="kyoto.ae")
    status, latest = public_status(product, product.heartbeat.effective_status())
    assert status == "ok" and latest is not None


@pytest.mark.django_db
def test_healthy_and_old_is_delayed():
    swxkit.publish_timeseries("kyoto.ae", _ae_rows(timezone.now() - dt.timedelta(days=13)))
    product = Product.objects.get(id="kyoto.ae")
    status, _latest = public_status(product, product.heartbeat.effective_status())
    assert status == "delayed"


@pytest.mark.django_db
def test_stale_wins_over_delayed():
    swxkit.publish_timeseries("kyoto.ae", _ae_rows(timezone.now() - dt.timedelta(days=13)))
    Heartbeat.objects.filter(product_id="kyoto.ae").update(last_seen_at=timezone.now() - dt.timedelta(days=31))
    product = Product.objects.get(id="kyoto.ae")
    assert public_status(product, product.heartbeat.effective_status())[0] == "stale"
    for status in ("error", "no_data"):
        assert public_status(product, status)[0] == status


@pytest.mark.django_db
def test_without_delayed_after_old_data_stays_ok():
    swxkit.publish_timeseries("kyoto.dst", [{"time_tag": timezone.now() - dt.timedelta(days=20), "dst": -9,
                                             "version": 0}])
    product = Product.objects.get(id="kyoto.dst")
    assert public_status(product, "ok") == ("ok", None)


@pytest.mark.django_db
@pytest.mark.parametrize("lang,label,tooltip", [("es", "al día · con retraso", "datos hasta 05/10"),
                                                ("en", "up to date · delayed", "data up to 05 Oct")])
def test_delayed_status_on_catalogue_pill_and_status_page(client, lang, label, tooltip):
    latest = dt.datetime(2026, 10, 5, 23, 55, tzinfo=dt.UTC)
    swxkit.publish_timeseries("kyoto.ae", _ae_rows(latest))
    for url in (_url("web:catalogue", lang=lang), _url("web:status", lang=lang),
                _url("web:product_detail", "kyoto.ae", lang=lang)):
        html = client.get(url).content.decode()
        assert label in html, url
        assert f'title="{tooltip}"' in html, url
        assert "var(--status-delayed)" in html, url


@pytest.mark.django_db
@pytest.mark.parametrize("lang,asof", [("es", "al 05/10 23:55 UT"), ("en", "as of 05 Oct 23:55 UT")])
def test_headline_carries_its_time_when_delayed(client, lang, asof):
    swxkit.publish_timeseries("kyoto.ae", _ae_rows(dt.datetime(2026, 10, 5, 23, 55, tzinfo=dt.UTC)))
    html = client.get(_url("web:product_detail", "kyoto.ae", lang=lang)).content.decode()
    assert f'<p class="now-asof">{asof}</p>' in html


@pytest.mark.django_db
def test_live_product_headline_has_no_asof(client):
    swxkit.publish_timeseries("kyoto.dst", [{"time_tag": timezone.now().replace(minute=0, second=0, microsecond=0),
                                             "dst": -9, "version": 0}])
    html = client.get(_url("web:product_detail", "kyoto.dst")).content.decode()
    assert "now-asof" not in html


def test_doi_forms_become_links_and_text_is_escaped():
    html = link_dois("Kyoto (2015), Geomagnetic AE index, doi:10.17593/15031-54800")
    assert html.endswith('<a href="https://doi.org/10.17593/15031-54800" rel="noopener">doi:10.17593/15031-54800</a>')
    html = link_dois("See 10.1029/2020JA028000. <script>")
    assert '<a href="https://doi.org/10.1029/2020JA028000" rel="noopener">10.1029/2020JA028000</a>.' in html
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert link_dois("1 0.5 nT, no DOI") == "1 0.5 nT, no DOI"


@pytest.mark.django_db
def test_citation_row_links_its_doi(client):
    swxkit.publish_timeseries("kyoto.dst", [{"time_tag": "2026-10-18T00:00:00Z", "dst": -10, "version": 0}])
    html = client.get(_url("web:product_detail", "kyoto.dst")).content.decode()
    assert '<a href="https://doi.org/10.17593/14515-74000" rel="noopener">doi:10.17593/14515-74000</a>' in html
