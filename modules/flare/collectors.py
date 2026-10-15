from __future__ import annotations

import httpx

from core import swxkit

from . import adapters, client


def collect_nrt() -> None:
    try:
        raw = client.fetch_status()
    except httpx.HTTPError:
        # The producer not running is expected before it's deployed (D-036:
        # the portal's flare card shows "en preparación" and launch is never
        # blocked on it) — "error" status, not an unhandled crash.
        swxkit.heartbeat("usach.flare.nrt", "error", detail="flare-nrt status endpoint unreachable")
        return

    parsed = adapters.parse_status(raw)
    if parsed is None:
        swxkit.heartbeat("usach.flare.nrt", "no_data", detail="flare-nrt has no result yet")
        return

    swxkit.publish_event(
        "usach.flare.nrt",
        parsed["payload"],
        valid_from=parsed["valid_from"],
        issued=parsed["issued"],
        quality=parsed["quality"],
    )


COLLECTORS = {"usach.flare.nrt": collect_nrt}
