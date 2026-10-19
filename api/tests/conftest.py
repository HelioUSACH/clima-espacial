import datetime as dt
from pathlib import Path

import pytest
from django.utils import timezone

from api import export
from api.keys import issue_key
from core import swxkit
from core.manifests.loader import discover_manifests, load_manifest
from core.models import Product, TimeSeriesPoint

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def key(db):
    """(ApiKey, plaintext) for a user key."""
    return issue_key(kind="user", label="test")


def minute_rows(end: dt.datetime, minutes: int, **fields):
    return [{"time_tag": end - dt.timedelta(minutes=i), "source": "SOLAR1",
             **{k: (v(i) if callable(v) else v) for k, v in fields.items()}} for i in range(minutes)]


@pytest.fixture
def recent_mag(db):
    """Two days of 1-min IMF ending at the last full minute."""
    end = timezone.now().replace(second=0, microsecond=0) - dt.timedelta(minutes=1)
    swxkit.publish_timeseries("noaa.rtsw.mag", minute_rows(end, 2 * 1440, bt=5.0, bx_gsm=1.0, by_gsm=-2.0,
                                                           bz_gsm=lambda i: -3.0 + i % 5))
    return end


@pytest.fixture
def own_product(db, monkeypatch):
    """usach.test.own (fixtures/own_product.yaml) added to the manifests the
    export layer sees, with a day of data ending now."""
    manifest = load_manifest(FIXTURES / "own_product.yaml")
    real = discover_manifests()
    monkeypatch.setattr(export, "discover_manifests", lambda: {**real, manifest["id"]: manifest})
    product = Product.objects.create(id=manifest["id"], kind="timeseries", title_es=manifest["title"]["es"],
                                     title_en=manifest["title"]["en"], maturity="experimental", cadence_seconds=60,
                                     stale_after_seconds=900, producer_type="collector",
                                     source_provider=manifest["source"]["provider"], module="test", current_version=1,
                                     exchange_hapi=True)
    end = timezone.now().replace(second=0, microsecond=0) - dt.timedelta(minutes=1)
    for i in range(120):
        t = end - dt.timedelta(minutes=i)
        swxkit._ensure_partition("core_timeseries", t)
        TimeSeriesPoint.objects.create(product=product, time_tag=t, payload={"dbdt": 1.5 * i, "probability": 0.25})
    return manifest, end
