"""`serie.json` bucketing, caching and output contract (D-056, D-057), plus
the range buttons that depend on the stored history (13-AUDIT §3)."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone, translation

from core import swxkit
from web import charts

from .test_panels import KP_MANIFEST, MAG_MANIFEST

WIND_MANIFEST = dict(
    MAG_MANIFEST, id="noaa.rtsw.wind", title={"es": "Viento solar (L1)", "en": "Solar wind (L1)"},
    parameters=[{"name": "proton_speed", "units": "km/s"}], _path="modules/swpc/manifests/rtsw_wind.yaml",
)
XRAY_MANIFEST = dict(
    MAG_MANIFEST, id="noaa.goes.xray", title={"es": "Rayos X", "en": "X-rays"}, cadence="1m",
    parameters=[{"name": "flux", "units": "W/m2"}], _path="modules/swpc/manifests/goes_xray.yaml",
)
MANIFESTS = {m["id"]: m for m in (MAG_MANIFEST, WIND_MANIFEST, XRAY_MANIFEST, KP_MANIFEST)}


@pytest.fixture
def manifests(monkeypatch):
    monkeypatch.setattr(swxkit, "get_manifest", lambda pid: dict(MANIFESTS[pid]))


def _now_minute():
    return timezone.now().replace(second=0, microsecond=0)


def _series(client, product_id, range_key):
    response = client.get(reverse("web:product_series_json", args=[product_id]) + f"?range={range_key}")
    assert response.status_code == 200
    return response


def _mag_row(t, bt=5.0, bz=-2.0):
    return {"time_tag": t, "source": "SOLAR1", "bt": bt, "bx_gsm": 1.0, "by_gsm": -1.0, "bz_gsm": bz}


@pytest.fixture
def week_of_mag(manifests):
    """7 days at 5-min cadence, then the last day at 1-min: at least as
    dense as every range's bucket, so each range's point budget is really
    tested (and kept to ~3,200 rows so the test stays quick)."""
    now = _now_minute()
    rows = [_mag_row(now - dt.timedelta(minutes=m), bt=4 + (m % 7)) for m in range(7 * 1440, 1440, -5)]
    rows += [_mag_row(now - dt.timedelta(minutes=m), bt=4 + (m % 7)) for m in range(1440, -1, -1)]
    swxkit.publish_timeseries("noaa.rtsw.mag", rows)


@pytest.mark.django_db
@pytest.mark.parametrize("range_key", ["6h", "1d", "3d", "7d"])
def test_points_per_series_within_bucket_budget(client, week_of_mag, range_key):
    payload = _series(client, "noaa.rtsw.mag", range_key).json()
    bucket = charts.BUCKET_SECONDS[range_key]
    budget = charts.RANGE_DAYS[range_key] * 86400 / bucket + 1
    assert payload["bucket_seconds"] == bucket
    assert 0.9 * budget <= len(payload["time"]) <= budget
    for key in ("series", "min", "max"):
        for values in payload[key].values():
            assert len(values) == len(payload["time"])
    assert len(payload["source"]) == len(payload["time"])
    assert all(b - a == bucket for a, b in zip(payload["time"], payload["time"][1:], strict=False))


@pytest.mark.django_db
def test_min_le_mean_le_max(client, week_of_mag):
    payload = _series(client, "noaa.rtsw.mag", "7d").json()
    for field, means in payload["series"].items():
        lows, highs = payload["min"][field], payload["max"][field]
        for lo, mean, hi in zip(lows, means, highs, strict=True):
            if mean is not None:
                assert lo <= mean <= hi
    # The 5-min data really varies inside a 15-min bucket: not a flat copy.
    assert any(lo < hi for lo, hi in zip(payload["min"]["bt"], payload["max"]["bt"], strict=True))


@pytest.mark.django_db
def test_fill_values_excluded_before_aggregating(client, manifests):
    now = _now_minute()
    # One 15-min bucket at 7d: five real Bz values and NOAA's -9999 fill.
    start = now - dt.timedelta(hours=2)
    start -= dt.timedelta(seconds=start.timestamp() % 900)
    rows = [_mag_row(start + dt.timedelta(minutes=m), bz=-2.0) for m in range(5)]
    rows.append(_mag_row(start + dt.timedelta(minutes=5), bz=-9999))
    rows.append(_mag_row(start + dt.timedelta(minutes=6), bz=-1e5))
    swxkit.publish_timeseries("noaa.rtsw.mag", rows)
    payload = _series(client, "noaa.rtsw.mag", "7d").json()
    assert payload["series"]["bz_gsm"] == [-2.0]
    assert payload["min"]["bz_gsm"] == [-2.0]


@pytest.mark.django_db
def test_bucket_with_only_fill_values_is_null(client, manifests):
    now = _now_minute()
    swxkit.publish_timeseries("noaa.rtsw.mag", [_mag_row(now - dt.timedelta(minutes=3), bz=-9999)])
    payload = _series(client, "noaa.rtsw.mag", "6h").json()
    assert payload["series"]["bz_gsm"] == [None]
    assert payload["series"]["bt"] == [5.0]  # the other fields of that row are still real


@pytest.mark.django_db
def test_real_gap_gives_null_buckets(client, manifests):
    now = _now_minute()
    rows = [_mag_row(now - dt.timedelta(minutes=m)) for m in range(120, 100, -1)]
    rows += [_mag_row(now - dt.timedelta(minutes=m)) for m in range(40, 20, -1)]
    swxkit.publish_timeseries("noaa.rtsw.mag", rows)
    payload = _series(client, "noaa.rtsw.mag", "1d").json()  # 2-min buckets
    bt = payload["series"]["bt"]
    assert bt[0] is not None and bt[-1] is not None
    nulls = [t for t, v in zip(payload["time"], bt, strict=True) if v is None]
    assert len(nulls) >= 25  # the ~60 min hole, one null per missing 2-min bucket
    assert payload["min"]["bt"][bt.index(None)] is None


@pytest.mark.django_db
def test_second_call_hits_the_cache(client, week_of_mag, django_assert_num_queries):
    first = _series(client, "noaa.rtsw.mag", "1d")
    with django_assert_num_queries(0):
        second = _series(client, "noaa.rtsw.mag", "1d")
    assert second.content == first.content
    assert "max-age=60" in second["Cache-Control"] and "public" in second["Cache-Control"]


@pytest.mark.django_db
def test_cache_is_per_range(client, week_of_mag):
    assert _series(client, "noaa.rtsw.mag", "6h").json()["bucket_seconds"] == 60
    assert _series(client, "noaa.rtsw.mag", "7d").json()["bucket_seconds"] == 900


@pytest.mark.django_db
def test_unknown_product_is_404_and_not_cached(client):
    url = reverse("web:product_series_json", args=["no.such.product"])
    assert client.get(url).status_code == 404
    assert client.get(url).status_code == 404


@pytest.mark.django_db
def test_energy_channels_stay_aligned(client, manifests):
    now = _now_minute()
    rows = []
    for m in range(30, 0, -1):
        t = now - dt.timedelta(minutes=m)
        rows.append({"time_tag": t, "energy": "0.1-0.8nm", "flux": 2.0e-6, "satellite": 18})
        if m % 2:  # the short channel is missing every other minute
            rows.append({"time_tag": t, "energy": "0.05-0.4nm", "flux": 1.234567e-7, "satellite": 18})
    swxkit.publish_timeseries("noaa.goes.xray", rows)
    payload = _series(client, "noaa.goes.xray", "6h").json()
    long_, short = payload["series"]["0.1-0.8nm"], payload["series"]["0.05-0.4nm"]
    assert len(long_) == len(short) == len(payload["time"]) == 30
    assert all(v == 2.0e-6 for v in long_)
    assert short[1::2] == [1.23e-7] * 15  # 3 significant figures
    assert short[::2] == [None] * 15


@pytest.mark.django_db
def test_rounding_per_unit(client, manifests):
    now = _now_minute()
    swxkit.publish_timeseries("noaa.rtsw.mag", [_mag_row(now - dt.timedelta(minutes=2), bt=4.56789)])
    swxkit.publish_timeseries(
        "noaa.rtsw.wind",
        [{"time_tag": now - dt.timedelta(minutes=2), "proton_speed": 412.3456, "proton_density": 3.14159,
          "proton_temperature": 123456}],
    )
    mag = _series(client, "noaa.rtsw.mag", "6h").json()
    wind = _series(client, "noaa.rtsw.wind", "6h").json()
    assert mag["series"]["bt"] == [4.57]  # nT -> 0.01
    assert wind["series"]["proton_speed"] == [412.3]  # km/s -> 0.1
    assert wind["series"]["proton_density"] == [3.14]  # p/cm3 -> 0.01
    assert wind["series"]["proton_temperature"] == [123000.0]  # K -> 3 significant figures


@pytest.mark.django_db
def test_response_shape(client, week_of_mag):
    payload = _series(client, "noaa.rtsw.mag", "3d").json()
    assert set(payload) == {"time", "series", "min", "max", "source", "bucket_seconds", "anchor"}
    assert payload["anchor"] == "now"
    assert set(payload["series"]) == set(payload["min"]) == set(payload["max"]) == {"bt", "bz_gsm", "bx_gsm", "by_gsm"}
    assert payload["source"][-1] == "SOLAR1"


# ---- range buttons vs stored history --------------------------------------

@pytest.mark.django_db
def test_ranges_longer_than_history_are_disabled(client, manifests):
    now = _now_minute()
    swxkit.publish_timeseries("noaa.rtsw.mag", [_mag_row(now - dt.timedelta(hours=36)), _mag_row(now)])
    response = client.get(reverse("web:product_detail", args=["noaa.rtsw.mag"]))
    ranges = {r["key"]: r for r in response.context["ranges"]}
    assert ranges["6h"]["enabled"] and ranges["1d"]["enabled"]
    assert not ranges["3d"]["enabled"] and not ranges["7d"]["enabled"]
    assert ranges["7d"]["title"].startswith("Datos disponibles desde ")
    assert ranges["7d"]["title"].endswith(" UT")
    # The product page's 3d default isn't available yet: fall back to 1d.
    assert response.context["default_range"] == "1d"
    html = response.content.decode()
    assert 'data-chart-range="7d" aria-pressed="false" aria-disabled="true" title="Datos disponibles desde' in html


@pytest.mark.django_db
def test_range_tooltip_in_english(client, manifests):
    now = _now_minute()
    swxkit.publish_timeseries("noaa.rtsw.mag", [_mag_row(now - dt.timedelta(hours=36)), _mag_row(now)])
    with translation.override("en"):
        response = client.get(reverse("web:product_detail", args=["noaa.rtsw.mag"]))
    title = {r["key"]: r for r in response.context["ranges"]}["7d"]["title"]
    assert title.startswith("Data available since ")
    since = now - dt.timedelta(hours=36)
    assert since.strftime("%b") in title  # "dd Mon", not mm/dd


@pytest.mark.django_db
def test_all_ranges_enabled_without_history(client):
    response = client.get(reverse("web:home"))
    assert all(r["enabled"] for r in response.context["ranges"])
    assert response.context["default_range"] == charts.DEFAULT_RANGE_HOME


@pytest.mark.django_db
def test_data_sparser_than_bucket_has_no_false_gaps(client, manifests):
    # 5-min samples in the 1d range's 2-min buckets land 4 or 6 minutes
    # apart: uneven, but not a gap (seen as a dashed proton line in v0.1.3
    # development screenshots before the 1.5x gap margin).
    now = _now_minute()
    now -= dt.timedelta(minutes=now.minute % 5)
    rows = []
    for m in range(300, 0, -5):
        rows.append({"time_tag": now - dt.timedelta(minutes=m), "energy": "0.1-0.8nm", "flux": 1e-6, "satellite": 18})
    swxkit.publish_timeseries("noaa.goes.xray", rows)
    payload = _series(client, "noaa.goes.xray", "1d").json()
    assert len(payload["time"]) == 60
    assert None not in payload["series"]["0.1-0.8nm"]
