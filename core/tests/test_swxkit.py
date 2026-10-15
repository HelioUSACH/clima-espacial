import datetime as dt

import pytest
from django.utils import timezone

from core import swxkit
from core.models import Heartbeat, Product, ProductVersion, TimeSeriesPoint

MANIFEST = {
    "id": "usach.test.widget",
    "version": 2,
    "kind": "timeseries",
    "title": {"es": "Prueba", "en": "Test"},
    "maturity": "experimental",
    "cadence": "60s",
    "stale_after": "15m",
    "producer": {"type": "collector"},
    "source": {"provider": "Test", "license": "CC-BY-4.0"},
    "parameters": [{"name": "value", "units": "1"}],
    "exchange": {"hapi": False, "feed": False},
    "_module": "demo",
    "_path": "modules/demo/manifests/widget.yaml",
}


@pytest.fixture(autouse=True)
def fake_manifest(monkeypatch):
    monkeypatch.setattr(swxkit, "get_manifest", lambda product_id: dict(MANIFEST))


@pytest.mark.django_db
def test_publish_timeseries_creates_product_and_points():
    n = swxkit.publish_timeseries(
        "usach.test.widget",
        [
            {"time_tag": "2026-10-15T00:00:00Z", "source": "TEST", "value": 1.0},
            {"time_tag": "2026-10-15T00:01:00Z", "source": "TEST", "value": 2.0},
        ],
    )
    assert n == 2
    product = Product.objects.get(id="usach.test.widget")
    assert product.title_es == "Prueba"
    assert product.stale_after_seconds == 900
    assert ProductVersion.objects.filter(product=product, version=2).exists()
    points = TimeSeriesPoint.objects.filter(product_id="usach.test.widget").order_by("time_tag")
    assert [p.payload["value"] for p in points] == [1.0, 2.0]
    assert points[0].source == "TEST"
    hb = Heartbeat.objects.get(product=product)
    assert hb.status == "ok"
    assert hb.effective_status() == "ok"


@pytest.mark.django_db
def test_publish_timeseries_rerun_with_no_new_rows_is_ok_not_no_data():
    rows = [{"time_tag": "2026-10-15T00:00:00Z", "value": 1.0}]
    assert swxkit.publish_timeseries("usach.test.widget", rows) == 1
    # A slow-cadence product polled often will often re-fetch the same window
    # with nothing new: that is healthy, not "no_data" (D-048 rationale).
    assert swxkit.publish_timeseries("usach.test.widget", rows) == 0
    hb = Heartbeat.objects.get(product_id="usach.test.widget")
    assert hb.status == "ok"
    assert TimeSeriesPoint.objects.filter(product_id="usach.test.widget").count() == 1


@pytest.mark.django_db
def test_publish_timeseries_empty_rows_marks_no_data():
    swxkit.publish_timeseries("usach.test.widget", [])
    hb = Heartbeat.objects.get(product_id="usach.test.widget")
    assert hb.status == "no_data"


@pytest.mark.django_db
def test_heartbeat_effective_status_goes_stale_when_old():
    swxkit.heartbeat("usach.test.widget", "ok")
    hb = Heartbeat.objects.get(product_id="usach.test.widget")
    hb.last_seen_at = timezone.now() - dt.timedelta(hours=1)
    hb.save(update_fields=["last_seen_at"])
    assert hb.effective_status() == "stale"


@pytest.mark.django_db
def test_heartbeat_rejects_unknown_status():
    with pytest.raises(swxkit.PublishError):
        swxkit.heartbeat("usach.test.widget", "fine")


@pytest.mark.django_db
def test_publish_timeseries_across_month_boundary_creates_two_partitions():
    from django.db import connection

    n = swxkit.publish_timeseries(
        "usach.test.widget",
        [
            {"time_tag": "2026-09-30T23:59:00Z", "value": 1.0},
            {"time_tag": "2026-10-01T00:01:00Z", "value": 2.0},
        ],
    )
    assert n == 2
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT inhrelid::regclass::text FROM pg_inherits "
            "WHERE inhparent = 'core_timeseries'::regclass ORDER BY 1"
        )
        partitions = {row[0] for row in cursor.fetchall()}
    assert "core_timeseries_y2026m09" in partitions
    assert "core_timeseries_y2026m10" in partitions
