"""D-081/D-082 on the web side: noaa.dst's retirement (301, hidden from the
catalogue and status), kyoto.dst's page, and kyoto.ae's latest-datum anchor."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from core import swxkit
from core.models import Product, TimeSeriesPoint
from web import series


@pytest.fixture
def retired_noaa_dst(db):
    # What helio has after deploying 0.4.2: noaa.dst's Product row and
    # history, with no manifest any more.
    product = Product.objects.create(id="noaa.dst", kind="index", title_es="Índice Dst (Kyoto)",
                                     title_en="Dst index (Kyoto)", maturity="operational", cadence_seconds=900,
                                     stale_after_seconds=10800, producer_type="collector",
                                     source_provider="NOAA SWPC (Kyoto WDC)", module="swpc", current_version=1)
    when = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
    swxkit._ensure_partition("core_timeseries", when)
    TimeSeriesPoint.objects.create(product=product, time_tag=when, payload={"dst": -9})
    return product


@pytest.mark.django_db
def test_noaa_dst_page_moves_permanently_to_kyoto_dst(client, retired_noaa_dst):
    response = client.get(reverse("web:product_detail", args=["noaa.dst"]))
    assert response.status_code == 301
    assert response["Location"] == reverse("web:product_detail", args=["kyoto.dst"])
    # Its history is still there.
    assert TimeSeriesPoint.objects.filter(product_id="noaa.dst").exists()


@pytest.mark.django_db
def test_retired_product_is_not_listed(client, retired_noaa_dst):
    swxkit.heartbeat("kyoto.dst", "ok")
    for name in ("web:catalogue", "web:status"):
        html = client.get(reverse(name)).content.decode()
        assert "productos/kyoto.dst/" in html, name
        assert "productos/noaa.dst/" not in html, name


@pytest.mark.django_db
def test_kyoto_dst_page_shows_citation_and_links(client):
    swxkit.publish_timeseries("kyoto.dst", [{"time_tag": "2026-10-18T00:00:00Z", "dst": -10, "version": 0,
                                              "source": "KYOTO-HAPI"}])
    html = client.get(reverse("web:product_detail", args=["kyoto.dst"])).content.decode()
    assert "<dt>Cita</dt><dd>World Data Center for Geomagnetism, Kyoto" in html
    assert "doi:10.17593/14515-74000" in html
    assert '<a class="pill" href="https://wdc.kugi.kyoto-u.ac.jp/dst_realtime/index.html"' in html
    assert "Uso no comercial; datos preliminares (WDC Kyoto)" in html


@pytest.mark.django_db
def test_delayed_product_window_ends_at_its_latest_datum(client):
    end = timezone.now().replace(second=0, microsecond=0) - dt.timedelta(days=13)
    rows = [{"time_tag": end - dt.timedelta(minutes=i), "ae": 200 + i, "al": -150, "au": 50, "ao": -50, "version": 0}
            for i in range(0, 600)]
    swxkit.publish_timeseries("kyoto.ae", rows)
    data = client.get(reverse("web:product_series_json", args=["kyoto.ae"]) + "?range=6h").json()
    assert data["anchor"] == "latest"
    assert data["time"], "a 6 h range anchored at now would be empty"
    assert data["time"][-1] <= int(end.timestamp()) and data["time"][-1] > int(end.timestamp()) - 600
    assert any(v is not None for v in data["series"]["ae"])


@pytest.mark.django_db
def test_live_products_still_end_now():
    t = timezone.now() - dt.timedelta(days=13)
    swxkit.publish_timeseries("kyoto.dst", [{"time_tag": t, "dst": -10, "version": 0}])
    data = series.bucketed_series("kyoto.dst", "1d")
    assert data["anchor"] == "now" and data["time"] == []


@pytest.mark.django_db
def test_ae_page_panels_carry_the_anchor(client):
    swxkit.heartbeat("kyoto.ae", "ok")
    html = client.get(reverse("web:product_detail", args=["kyoto.ae"])).content.decode()
    assert html.count('"anchor": "latest"') == 2
    assert '"delayed_suffix": "(con retraso)"' in html
