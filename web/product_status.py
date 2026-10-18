"""A product's public status, from its heartbeat plus, for a product whose
manifest sets `delayed_after` (D-085), the age of its newest stored datum.

`Heartbeat.effective_status()` says whether *our* producer is healthy. A
product can be healthy and still be behind: kyoto.ae's collector runs every
day without error, but Kyoto's HAPI copy stops weeks back (D-082). That
reads "al día · con retraso" instead of "al día". `stale`, `error` and
`no_data` always win over it: delayed is only ever a refinement of `ok`.
"""

from __future__ import annotations

import datetime as dt

from django.db.models import Max
from django.utils import timezone

from core.models import Event, Forecast, TimeSeriesPoint


def latest_datum(product) -> dt.datetime | None:
    """The time of the newest stored datum: a sample's `time_tag`, an
    event's `issued`, a forecast's `issued_at`."""
    if product.kind == "event":
        return Event.objects.filter(product_id=product.id).aggregate(t=Max("issued"))["t"]
    if product.kind == "forecast":
        return Forecast.objects.filter(product_id=product.id).aggregate(t=Max("issued_at"))["t"]
    return TimeSeriesPoint.objects.filter(product_id=product.id).aggregate(t=Max("time_tag"))["t"]


def public_status(product, effective_status: str, *, latest: dt.datetime | None = None,
                  now: dt.datetime | None = None) -> tuple[str, dt.datetime | None]:
    """(status, latest datum) where status is the heartbeat's effective
    status, or "delayed" when that is "ok" but the newest datum is older
    than the product's `delayed_after`. `latest` is looked up only when
    needed (only a product with `delayed_after` pays for the query)."""
    if effective_status != "ok" or not product.delayed_after_seconds:
        return effective_status, latest
    if latest is None:
        latest = latest_datum(product)
    if latest is None:
        return effective_status, None
    age = ((now or timezone.now()) - latest).total_seconds()
    return ("delayed" if age > product.delayed_after_seconds else effective_status), latest
