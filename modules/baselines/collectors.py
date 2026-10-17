"""The baselines' producer: reads the observed target through
`swxkit.read_series`, issues through `swxkit.publish_forecast` (the worked
example in docs/models/INTEGRATION.md)."""

from __future__ import annotations

import datetime as dt

from django.utils import timezone

from core import swxkit
from core.manifests.loader import get_manifest, parse_duration

from . import persistence

PRODUCT_ID = "usach.kp.persistence"


def collect_kp_persistence(issued_at: dt.datetime | None = None, observations=None) -> int:
    """One issue. `issued_at` defaults to now floored to the manifest cadence
    (the hour); `observations` lets `seed_dev_data` replay many issues from
    one read instead of re-reading 30 days per issue."""
    manifest = get_manifest(PRODUCT_ID)
    spec = manifest["forecast"]
    target, field = spec["target"]["product"], spec["target"]["field"]
    horizons = [parse_duration(h) for h in spec["horizons"]]
    # The deterministic issue time (D-073): the hour, whenever the timer fired.
    issued_at = swxkit.floor_time(issued_at or timezone.now(), parse_duration(manifest["cadence"]))
    if observations is None:
        since = issued_at - persistence.ERROR_WINDOW - dt.timedelta(seconds=max(horizons)) - dt.timedelta(days=1)
        observations = swxkit.read_series(target, field, since)
    try:
        rows, newest_used = persistence.issue(observations, issued_at, horizons, field)
    except persistence.TargetStale as exc:
        # A dead input must not be persisted as a live forecast.
        swxkit.heartbeat(PRODUCT_ID, "error", detail=f"target stale: {exc}")
        return 0
    return swxkit.publish_forecast(
        PRODUCT_ID,
        issued_at=issued_at,
        model_version=persistence.MODEL_VERSION,
        rows=rows,
        provenance={"inputs": [{"product": target, "latest": newest_used.strftime("%Y-%m-%dT%H:%M:%SZ")}],
                    "producer": persistence.PRODUCER},
    )


COLLECTORS = {
    PRODUCT_ID: collect_kp_persistence,
}
