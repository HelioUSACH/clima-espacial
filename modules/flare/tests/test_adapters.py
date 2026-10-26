import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

from core.models import Event, Heartbeat
from modules.flare import adapters, client, collectors

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def test_parse_status_live():
    parsed = adapters.parse_status(_load("status_live.json"))
    assert parsed is not None
    assert parsed["payload"]["probability"] == 0.0912
    assert parsed["payload"]["flare_detected"] is False
    assert "location" in parsed["payload"]
    assert "severity" not in parsed["payload"]  # 01-AUDIT.md §3.6: never republished
    assert parsed["quality"]["producer_live_status"] == "live"
    assert parsed["issued"] == dt.datetime(2026, 10, 15, 1, 59, 21, tzinfo=dt.UTC)


def test_parse_status_no_result_returns_none():
    assert adapters.parse_status(_load("status_no_result.json")) is None


@pytest.mark.parametrize("value, expected", [
    # What the producer sends today: naive, meaning UTC (D-096).
    ("2026-10-15T01:59:21.483912", dt.datetime(2026, 10, 15, 1, 59, 21, 483912, tzinfo=dt.UTC)),
    ("2026-10-15T01:59:21Z", dt.datetime(2026, 10, 15, 1, 59, 21, tzinfo=dt.UTC)),
    ("2026-10-14T22:59:21-03:00", dt.datetime(2026, 10, 15, 1, 59, 21, tzinfo=dt.UTC)),
])
def test_parse_time_naive_is_utc_and_offsets_are_kept(value, expected):
    parsed = adapters.parse_time(value)
    assert parsed == expected
    assert parsed.utcoffset() == dt.timedelta(0)


def test_parse_status_naive_timestamp():
    parsed = adapters.parse_status(_load("status_live_naive.json"))
    assert parsed["issued"] == dt.datetime(2026, 10, 15, 1, 59, 21, 483912, tzinfo=dt.UTC)


@pytest.mark.parametrize("raw, expected", [
    ({"live_status": "live"}, "live"),
    ({"live_status": "stale"}, "stale"),
    ({"live_status": "no_data"}, "no_data"),
    ({"live_status": "error"}, "error"),
    ({}, "unknown"),  # missing
    ({"live_status": None}, "unknown"),
    ({"live_status": "warming_up"}, "unknown"),  # a value this adapter doesn't know
])
def test_live_status(raw, expected):
    assert adapters.live_status(raw) == expected


@pytest.fixture
def producer(monkeypatch):
    """The producer's /api/status answers whatever `state["raw"]` holds."""
    state = {"raw": None}

    def fetch_status():
        if state["raw"] is None:
            raise httpx.ConnectError("refused")
        return state["raw"]

    monkeypatch.setattr(client, "fetch_status", fetch_status)
    return state


def _heartbeat():
    return Heartbeat.objects.get(product_id="usach.flare.nrt")


def _events():
    return Event.objects.filter(product_id="usach.flare.nrt")


@pytest.mark.django_db
def test_same_issued_twice_is_stored_once(producer):
    producer["raw"] = _load("status_live.json")
    collectors.collect_nrt()
    collectors.collect_nrt()
    assert _events().count() == 1
    assert _heartbeat().status == "ok"  # the producer is live, just not newer

    producer["raw"] = dict(producer["raw"], timestamp="2026-10-15T02:02:21Z")
    collectors.collect_nrt()
    assert _events().count() == 2


@pytest.mark.django_db
def test_naive_timestamp_polled_twice_is_stored_once_as_utc(producer):
    producer["raw"] = _load("status_live_naive.json")
    collectors.collect_nrt()
    collectors.collect_nrt()
    assert list(_events().values_list("issued", flat=True)) == [
        dt.datetime(2026, 10, 15, 1, 59, 21, 483912, tzinfo=dt.UTC)]


@pytest.mark.django_db
def test_stale_producer_publishes_nothing_and_says_how_old(producer):
    producer["raw"] = _load("status_stale.json")
    collectors.collect_nrt()
    assert not _events().exists()
    hb = _heartbeat()
    assert hb.status == "stale"
    assert "1843.7" in hb.detail


@pytest.mark.django_db
def test_stale_after_live_keeps_the_last_event_but_turns_stale(producer):
    producer["raw"] = _load("status_live.json")
    collectors.collect_nrt()
    producer["raw"] = _load("status_stale.json")
    collectors.collect_nrt()
    assert _events().count() == 1
    assert _heartbeat().status == "stale"


@pytest.mark.django_db
@pytest.mark.parametrize("live_status", ["no_data", "unknown", None, "warming_up"])
def test_no_data_or_unknown_publishes_nothing(producer, live_status):
    raw = _load("status_live.json")
    if live_status is None:
        del raw["live_status"]
    else:
        raw["live_status"] = live_status
    producer["raw"] = raw
    collectors.collect_nrt()
    assert not _events().exists()
    assert _heartbeat().status == "no_data"


@pytest.mark.django_db
def test_producer_error_status(producer):
    producer["raw"] = dict(_load("status_live.json"), live_status="error")
    collectors.collect_nrt()
    assert not _events().exists()
    assert _heartbeat().status == "error"


@pytest.mark.django_db
def test_live_without_result_is_no_data(producer):
    producer["raw"] = dict(_load("status_no_result.json"), live_status="live")
    collectors.collect_nrt()
    assert not _events().exists()
    assert _heartbeat().status == "no_data"


@pytest.mark.django_db
def test_unreachable_producer_is_error(producer):
    collectors.collect_nrt()
    assert _heartbeat().status == "error"
