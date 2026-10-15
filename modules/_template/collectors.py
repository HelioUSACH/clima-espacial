"""Collector(s) for this module. `manage.py collect <product_id>` looks up
`COLLECTORS[<product_id>]` and calls it with no arguments; it must publish
through `core.swxkit` and return the number of points/events published (or
None if that concept doesn't apply, e.g. a single event)."""

from core import swxkit


def collect_example() -> int:
    # Replace with a real fetch (httpx.get(...).json(), parse, map to the
    # manifest's `parameters`) and a real freshness story. See
    # modules/swpc/collectors.py for a worked example.
    rows = [{"time_tag": "2026-01-01T00:00:00Z", "example_value": 0}]
    return swxkit.publish_timeseries("usach.MODULE_NAME.example", rows)


COLLECTORS = {
    "usach.MODULE_NAME.example": collect_example,
}
