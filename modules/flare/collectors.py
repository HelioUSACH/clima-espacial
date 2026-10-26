from __future__ import annotations

import httpx

from core import swxkit
from core.models import Event

from . import adapters, client

PRODUCT_ID = "usach.flare.nrt"


def collect_nrt() -> None:
    try:
        raw = client.fetch_status()
    except httpx.HTTPError:
        # The producer not running is expected before it's deployed (D-036:
        # the portal's flare card shows "en preparación" and launch is never
        # blocked on it) — "error" status, not an unhandled crash.
        swxkit.heartbeat(PRODUCT_ID, "error", detail="flare-nrt status endpoint unreachable")
        return

    # D-096: a dead model must not look healthy. Only a live producer's
    # result is published; the service answering is not enough.
    status = adapters.live_status(raw)
    if status == "stale":
        swxkit.heartbeat(PRODUCT_ID, "stale",
                         detail=f"flare-nrt is stale: {raw.get('seconds_since_update')} s since its last update")
        return
    if status == "error":
        swxkit.heartbeat(PRODUCT_ID, "error", detail="flare-nrt reports live_status error")
        return
    if status != "live":
        swxkit.heartbeat(PRODUCT_ID, "no_data", detail=f"flare-nrt live_status {status}")
        return

    parsed = adapters.parse_status(raw)
    if parsed is None:
        swxkit.heartbeat(PRODUCT_ID, "no_data", detail="flare-nrt has no result yet")
        return

    # The producer updates every ~3 min and this runs every minute: the
    # same result is seen two or three times. Store it once (D-096).
    if Event.objects.filter(product_id=PRODUCT_ID, issued=parsed["issued"]).exists():
        swxkit.heartbeat(PRODUCT_ID, "ok", detail="no new result since the last poll")
        return

    swxkit.publish_event(
        PRODUCT_ID,
        parsed["payload"],
        valid_from=parsed["valid_from"],
        issued=parsed["issued"],
        quality=parsed["quality"],
    )


COLLECTORS = {PRODUCT_ID: collect_nrt}
