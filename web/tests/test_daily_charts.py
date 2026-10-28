"""Per-product chart ranges, caption and headline (D-097): a daily product
(noaa.goes.electron_fluence) offers 30 días / 90 días / 1 año / todo, says
its points are daily values, and dates its headline by day."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from core import swxkit
from web import charts

FLUENCE = "noaa.goes.electron_fluence"


def _today():
    return timezone.now().astimezone(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _publish_days(n, last_day=None):
    """`n` completed UTC days ending at `last_day` (default: yesterday)."""
    last_day = last_day or _today() - dt.timedelta(days=1)
    rows = [{"time_tag": last_day - dt.timedelta(days=i), "fluence": 1e7 + i * 1e5, "satellite": 19}
            for i in range(n - 1, -1, -1)]
    swxkit.publish_timeseries(FLUENCE, rows)
    return rows


def _page(client, product_id, lang="es"):
    prefix = "/en" if lang == "en" else ""
    response = client.get(f"{prefix}/productos/{product_id}/")
    assert response.status_code == 200
    return response


def _series(client, product_id, range_key):
    return client.get(reverse("web:product_series_json", args=[product_id]), {"range": range_key}).json()


def test_every_chart_spec_is_consistent():
    known = [key for key, *_rest in charts.RANGE_CHOICES]
    for spec in [charts.DEFAULT_CHART, *charts.PRODUCT_CHARTS.values()]:
        assert spec.ranges and spec.default_range in spec.ranges
        assert all(key in charts.BUCKET_SECONDS for key in spec.ranges)
        assert list(spec.ranges) == [k for k in known if k in spec.ranges]  # shortest first
    for product_id in charts.PRODUCT_CHARTS:
        assert product_id in charts.PRODUCT_PANELS


def test_daily_buckets_never_average_below_a_year():
    for key in charts.DAILY_CHART.ranges:
        assert charts.BUCKET_SECONDS[key] <= 86400


@pytest.mark.django_db
def test_daily_product_offers_long_ranges_default_90d(client):
    _publish_days(120)
    ctx = _page(client, FLUENCE).context
    assert [r["key"] for r in ctx["ranges"]] == ["30d", "90d", "1y", "all"]
    assert [r["label"] for r in ctx["ranges"]] == ["30 días", "90 días", "1 año", "todo"]
    assert [r["enabled"] for r in ctx["ranges"]] == [True, True, False, True]
    assert ctx["default_range"] == "90d"
    en = _page(client, FLUENCE, "en").context
    assert [r["label"] for r in en["ranges"]] == ["30 days", "90 days", "1 year", "all"]


@pytest.mark.django_db
def test_daily_product_with_a_week_defaults_to_30d_not_todo(client):
    _publish_days(8)
    ctx = _page(client, FLUENCE).context
    assert [r["enabled"] for r in ctx["ranges"]] == [True, False, False, True]
    assert ctx["default_range"] == "30d"


@pytest.mark.django_db
def test_other_products_keep_the_short_ranges(client):
    swxkit.publish_timeseries("noaa.rtsw.mag", [
        {"time_tag": timezone.now() - dt.timedelta(minutes=m), "source": "SOLAR1",
         "bt": 5.0, "bx_gsm": 1.0, "by_gsm": -1.0, "bz_gsm": -2.0} for m in range(10, 0, -1)])
    ctx = _page(client, "noaa.rtsw.mag").context
    assert [r["key"] for r in ctx["ranges"]] == ["6h", "1d", "3d", "7d"]
    assert ctx["panels"][0]["caption"] is None and ctx["panels"][0]["daily"] is False
    # A range the product doesn't offer falls back to its default, not a 1-year scan.
    assert _series(client, "noaa.rtsw.mag", "1y")["bucket_seconds"] == charts.BUCKET_SECONDS["3d"]


@pytest.mark.django_db
@pytest.mark.parametrize("range_key, days", [("30d", 30), ("90d", 90), ("1y", 365)])
def test_daily_series_is_one_point_per_day_unaveraged(client, range_key, days):
    rows = _publish_days(400)
    data = _series(client, FLUENCE, range_key)
    times = data["time"]
    assert days - 1 <= len(times) <= days + 1
    assert all(t % 86400 == 0 for t in times)  # at 00:00 UT, as stored
    assert all(b - a == 86400 for a, b in zip(times, times[1:], strict=False))
    stored = {int(r["time_tag"].timestamp()): float(f"{r['fluence']:.3g}") for r in rows}
    assert data["series"]["fluence"] == [stored[t] for t in times]


@pytest.mark.django_db
def test_daily_todo_returns_everything_stored(client):
    _publish_days(400)
    assert len(_series(client, FLUENCE, "all")["time"]) == 400
    # Not a range this product offers: its default (90 días).
    assert 89 <= len(_series(client, FLUENCE, "6h")["time"]) <= 91  # edges move with the clock


@pytest.mark.django_db
def test_daily_caption_from_the_product(client):
    _publish_days(3)
    assert _page(client, FLUENCE).context["panels"][0]["caption"] == "valores diarios (días UTC completos)"
    assert _page(client, FLUENCE, "en").context["panels"][0]["caption"] == "daily values (complete UTC days)"
    assert _page(client, FLUENCE).context["panels"][0]["daily"] is True
    assert _page(client, FLUENCE).context["chart_i18n"]["day_label"] == "día"
    assert _page(client, FLUENCE, "en").context["chart_i18n"]["day_label"] == "day"


@pytest.mark.django_db
def test_daily_headline_names_the_day_not_a_time(client):
    last_day = _today() - dt.timedelta(days=1)
    _publish_days(3, last_day)
    es = _page(client, FLUENCE).context["headline_asof"]
    en = _page(client, FLUENCE, "en").context["headline_asof"]
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    assert es == f"día {last_day:%d/%m}"
    assert en == f"day {last_day.day:02d} {months[last_day.month - 1]}"
    assert "UT" not in es and ":" not in es


@pytest.mark.django_db
def test_delayed_daily_headline_still_names_the_day(client):
    last_day = _today() - dt.timedelta(days=10)  # older than delayed_after: 3d
    _publish_days(3, last_day)
    swxkit.heartbeat(FLUENCE, "ok")
    ctx = _page(client, FLUENCE).context
    assert ctx["status"]["status"] == "delayed"
    assert ctx["headline_asof"] == f"día {last_day:%d/%m}"
