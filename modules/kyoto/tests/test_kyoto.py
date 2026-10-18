"""modules/kyoto against recorded HAPI payloads (tests/fixtures/README.md):
the CSV adapter, the NOAA fallback, the collectors' routes and errors."""

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

from core.models import Heartbeat, TimeSeriesPoint
from modules.kyoto import adapters, client, collectors

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UTC = dt.UTC


def _info(name):
    return json.loads((FIXTURES / f"info_{name}.json").read_text())


def _csv(name):
    return (FIXTURES / f"{name}.csv").read_text()


def test_dst_csv_centre_stamps_become_interval_starts():
    rows = adapters.parse_csv(_info("hour_dst_realtime"), _csv("hour_dst_realtime"))
    assert len(rows) == 8
    assert rows[0] == {"time_tag": dt.datetime(2026, 10, 17, 18, tzinfo=UTC), "source": "KYOTO-HAPI",
                       "dst": -8, "version": 0}
    assert rows[-1]["time_tag"] == dt.datetime(2026, 10, 18, 1, tzinfo=UTC) and rows[-1]["dst"] == -16


def test_ae_csv_keeps_the_minute_and_nulls_fill():
    rows = adapters.parse_csv(_info("min_ae"), _csv("min_ae"))
    assert rows[0] == {"time_tag": dt.datetime(2026, 10, 4, 23, 50, tzinfo=UTC), "source": "KYOTO-HAPI",
                       "ae": 252, "al": -185, "au": 67, "ao": -59, "version": 0}
    # 23:53 is all fill: dropped. 23:54 keeps what it has.
    assert [r["time_tag"].minute for r in rows] == [50, 51, 52, 54]
    assert rows[-1] == {"time_tag": dt.datetime(2026, 10, 4, 23, 54, tzinfo=UTC), "source": "KYOTO-HAPI",
                        "ae": 251, "al": None, "au": 70, "ao": None, "version": 0}


def test_csv_with_a_wrong_column_count_is_an_error():
    with pytest.raises(ValueError, match="expected 3 columns"):
        adapters.parse_csv(_info("hour_dst_realtime"), "2026-10-17T18:29:30Z,-8\n")


def test_empty_csv_is_no_rows():
    assert adapters.parse_csv(_info("min_ae"), "") == []


@pytest.mark.parametrize("value, seconds", [("PT1H", 3600), ("PT1M", 60), ("PT2M30S", 150)])
def test_cadence(value, seconds):
    assert adapters.cadence_seconds(value) == seconds


def test_noaa_mirror_rows_rank_below_any_kyoto_version():
    raw = json.loads((FIXTURES / "noaa_kyoto_dst.json").read_text())
    rows = adapters.parse_noaa_mirror(raw)
    assert len(rows) == len(raw)
    assert rows[0] == {"time_tag": raw[0]["time_tag"] + "Z", "source": "NOAA-MIRROR", "dst": raw[0]["dst"], "version": -1}


# --- client: clamping and errors, with httpx stubbed ------------------------

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return self._body if not isinstance(self._body, str) else json.loads(self._body)


def test_fetch_clamps_stop_to_the_servers_stop_date(monkeypatch):
    calls = []

    def fake_get(url, params, **kwargs):
        calls.append((url, params, kwargs["headers"]["User-Agent"]))
        return _Resp(200, _info("hour_dst_realtime")) if url.endswith("/info") else _Resp(200, _csv("hour_dst_realtime"))
    monkeypatch.setattr(httpx, "get", fake_get)
    meta, text = client.fetch("hour_dst_realtime", dt.datetime(2026, 10, 17, 18, tzinfo=UTC),
                              dt.datetime(2026, 10, 18, 9, tzinfo=UTC))
    assert calls[1][1] == {"dataset": "hour_dst_realtime", "start": "2026-10-17T18:00:00Z", "stop": "2026-10-18T02:29:30Z"}
    assert "ClimaEspacialUSACH" in calls[1][2]
    assert text.startswith("2026-10-17T18:29:30Z")


def test_hapi_error_status_raises_with_the_servers_message(monkeypatch):
    error = {"HAPI": "3.3.1", "status": {"code": 1405, "message": "Bad request - start < startDate and/or stop > stopDate."}}
    monkeypatch.setattr(httpx, "get", lambda url, params, **kw: _Resp(400, error))
    with pytest.raises(client.HapiError, match="HAPI 1405 Bad request"):
        client.info("hour_dst_realtime")


def test_network_errors_are_retried_once(monkeypatch):
    attempts = []

    def flaky(url, params, **kwargs):
        attempts.append(url)
        if len(attempts) == 1:
            raise httpx.ConnectTimeout("slow")
        return _Resp(200, _info("min_ae"))
    monkeypatch.setattr(httpx, "get", flaky)
    assert client.info("min_ae")["cadence"] == "PT1M"
    assert len(attempts) == 2


def test_two_network_errors_raise(monkeypatch):
    def down(url, params, **kwargs):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "get", down)
    with pytest.raises(client.HapiError, match="ConnectError"):
        client.info("min_ae")


# --- collectors ---------------------------------------------------------------

NOW = dt.datetime(2026, 10, 18, 1, 57, tzinfo=UTC)


@pytest.fixture
def kyoto_ok(monkeypatch):
    monkeypatch.setattr(client, "fetch", lambda ds, start, stop: (_info("hour_dst_realtime"), _csv("hour_dst_realtime")))


@pytest.fixture
def kyoto_down(monkeypatch):
    def fail(ds, start, stop):
        raise client.HapiError("info: ConnectError: refused")
    monkeypatch.setattr(client, "fetch", fail)


@pytest.fixture
def noaa(monkeypatch):
    calls = []

    def fetch_json(path):
        calls.append(path)
        return json.loads((FIXTURES / "noaa_kyoto_dst.json").read_text())
    monkeypatch.setattr(collectors.swpc_client, "fetch_json", fetch_json)
    return calls


@pytest.mark.django_db
def test_dst_prefers_kyoto(kyoto_ok, noaa):
    assert collectors.collect_dst(NOW) == 8
    assert noaa == []
    assert set(TimeSeriesPoint.objects.filter(product_id="kyoto.dst").values_list("source", flat=True)) == {"KYOTO-HAPI"}


@pytest.mark.django_db
def test_dst_falls_back_to_noaa_when_kyoto_fails(kyoto_down, noaa):
    assert collectors.collect_dst(NOW) > 0
    assert noaa == ["products/kyoto-dst.json"]
    point = TimeSeriesPoint.objects.filter(product_id="kyoto.dst").first()
    assert point.source == "NOAA-MIRROR" and point.payload["version"] == -1
    assert Heartbeat.objects.get(product_id="kyoto.dst").status == "ok"


@pytest.mark.django_db
def test_dst_falls_back_when_kyoto_is_empty(monkeypatch, noaa):
    monkeypatch.setattr(client, "fetch", lambda ds, start, stop: (_info("hour_dst_realtime"), ""))
    assert collectors.collect_dst(NOW) > 0
    assert noaa


@pytest.mark.django_db
def test_kyoto_replaces_a_noaa_value_for_the_same_hour(monkeypatch, kyoto_ok):
    hour = dt.datetime(2026, 10, 18, 0, tzinfo=UTC)
    monkeypatch.setattr(collectors.swpc_client, "fetch_json",
                        lambda path: [{"time_tag": "2026-10-18T00:00:00", "dst": -15}])
    monkeypatch.setattr(client, "fetch", lambda ds, start, stop: (_ for _ in ()).throw(client.HapiError("down")))
    collectors.collect_dst(NOW)
    monkeypatch.setattr(client, "fetch", lambda ds, start, stop: (_info("hour_dst_realtime"), _csv("hour_dst_realtime")))
    collectors.collect_dst(NOW)
    point = TimeSeriesPoint.objects.get(product_id="kyoto.dst", time_tag=hour)
    assert point.payload == {"dst": -10, "version": 0} and point.source == "KYOTO-HAPI"
    assert point.revised_at is not None


@pytest.mark.django_db
def test_both_routes_down_is_a_heartbeat_error_not_a_crash(kyoto_down, monkeypatch):
    def down(path):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(collectors.swpc_client, "fetch_json", down)
    assert collectors.collect_dst(NOW) == 0
    hb = Heartbeat.objects.get(product_id="kyoto.dst")
    assert hb.status == "error" and "Kyoto HAPI" in hb.detail and "NOAA mirror" in hb.detail


@pytest.mark.django_db
def test_ae_error_is_a_heartbeat(kyoto_down):
    assert collectors.collect_ae(NOW) == 0
    assert Heartbeat.objects.get(product_id="kyoto.ae").status == "error"


@pytest.mark.django_db
def test_ae_publishes(monkeypatch):
    monkeypatch.setattr(client, "fetch", lambda ds, start, stop: (_info("min_ae"), _csv("min_ae")))
    assert collectors.collect_ae(NOW) == 4
    assert TimeSeriesPoint.objects.filter(product_id="kyoto.ae").count() == 4


@pytest.mark.django_db
def test_backfill_revises_older_hours(monkeypatch, kyoto_ok):
    collectors.collect_dst(NOW)
    hour = dt.datetime(2026, 10, 17, 18, tzinfo=UTC)
    TimeSeriesPoint.objects.filter(product_id="kyoto.dst", time_tag=hour).update(payload={"dst": -6, "version": 0})
    collectors.collect_dst(NOW)  # 18:00 is outside latest (01:00) - 6h: left alone
    assert TimeSeriesPoint.objects.get(product_id="kyoto.dst", time_tag=hour).payload["dst"] == -6
    collectors.backfill("kyoto.dst", "hour_dst_realtime", NOW - dt.timedelta(days=1), NOW, dt.timedelta(days=10))
    assert TimeSeriesPoint.objects.get(product_id="kyoto.dst", time_tag=hour).payload["dst"] == -8
