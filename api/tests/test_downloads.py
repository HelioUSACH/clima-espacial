"""CSV downloads (§3) and the forecast export in the D-076 format (§5)."""

import csv
import datetime as dt
import io
import json

import pytest
from django.utils import timezone

from api import export
from api.models import ApiUsageDay
from api.views import EXCHANGE_FIELDS
from core import swxkit


def _iso(t):
    return export.fmt_time(t)


def _body(response):
    return b"".join(response.streaming_content).decode()


@pytest.mark.django_db
def test_csv_download_header_block_then_csv(client, recent_mag):
    stop = recent_mag + dt.timedelta(minutes=1)
    r = client.get("/api/v1/datos/noaa.rtsw.mag.csv", {"start": _iso(stop - dt.timedelta(minutes=3)),
                                                         "stop": _iso(stop)})
    assert r.status_code == 200 and r["Content-Type"].startswith("text/csv")
    assert r["Cache-Control"] == "no-store"
    assert r["Content-Disposition"].startswith('attachment; filename="noaa.rtsw.mag_')
    text = _body(r)
    comments = [line for line in text.splitlines() if line.startswith("#")]
    data = [line for line in text.splitlines() if not line.startswith("#")]
    assert "# Product page: http://testserver/productos/noaa.rtsw.mag/" in comments
    assert any("Licence: Public domain (NOAA)" in c for c in comments)
    assert any("may be withdrawn" in c for c in comments)
    assert any(c.startswith("# Columns: bt [nT]") for c in comments)
    rows = list(csv.reader(io.StringIO("\n".join(data))))
    assert rows[0] == ["time", "bt", "bx_gsm", "by_gsm", "bz_gsm"]
    assert len(rows) == 4 and rows[-1][0] == _iso(recent_mag)
    assert ApiUsageDay.objects.get().endpoint == "download"


@pytest.mark.django_db
def test_csv_download_needs_a_key_beyond_the_preview(client, recent_mag, key):
    stop = recent_mag + dt.timedelta(minutes=1)
    window = {"start": _iso(stop - dt.timedelta(days=2)), "stop": _iso(stop)}
    r = client.get("/api/v1/datos/noaa.rtsw.mag.csv", window)
    assert r.status_code == 401 and "/datos-api/" in r.json()["error"]
    _obj, raw = key
    r = client.get("/api/v1/datos/noaa.rtsw.mag.csv", window, HTTP_X_API_KEY=raw)
    assert r.status_code == 200 and len(_body(r).splitlines()) > 2 * 1440


@pytest.mark.django_db
def test_csv_download_errors(client, recent_mag, key):
    _obj, raw = key
    assert client.get("/api/v1/datos/kyoto.ae.csv", {"start": "2026-10-01", "stop": "2026-10-02"}).status_code == 404
    assert client.get("/api/v1/datos/noaa.rtsw.mag.csv", {"start": "2026-10-01"}).status_code == 400
    r = client.get("/api/v1/datos/noaa.rtsw.mag.csv", {"start": "2026-01-01", "stop": "2026-10-02"},
                   HTTP_X_API_KEY=raw)
    assert r.status_code == 400 and "31 days" in r.json()["error"]


@pytest.mark.django_db
def test_own_product_downloads(client, own_product):
    _manifest, end = own_product
    r = client.get("/api/v1/datos/usach.test.own.csv", {"start": _iso(end), "stop": _iso(end + dt.timedelta(minutes=1))})
    text = _body(r)
    assert "convenience" not in text and "# Licence: CC-BY-4.0" in text
    assert text.splitlines()[-2:] == ["time,dbdt,probability", f"{_iso(end)},0.0,0.25"]


# --- forecasts ------------------------------------------------------------------------

@pytest.fixture
def two_issues(db):
    issued = timezone.now().replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=1)
    for when, value in ((issued - dt.timedelta(hours=1), 2.0), (issued, 2.333)):
        swxkit.publish_forecast(
            "usach.kp.persistence", issued_at=when, model_version="0.1.0",
            rows=[{"valid_at": when + dt.timedelta(hours=h), "kp": {"value": value, "lower": 1.0, "upper": 4.0}}
                  for h in (3, 6, 12, 24)],
            provenance={"inputs": [{"product": "noaa.kp", "latest": "2026-10-17T15:00:00Z"}],
                        "producer": "baselines.kp_persistence@0.1.0"})
    return issued


@pytest.mark.django_db
def test_forecast_latest_issue_json_is_the_d076_format(client, two_issues):
    r = client.get("/api/v1/pronosticos/usach.kp.persistence.json")
    assert r.status_code == 200
    body = r.json()
    assert body["license"] == "CC-BY-4.0" and body["product"] == "usach.kp.persistence"
    records = body["records"]
    assert len(records) == 4
    assert all(list(rec) == list(EXCHANGE_FIELDS) for rec in records)
    first = records[0]
    assert first == {
        "model_id": "usach.kp.persistence", "model_version": "0.1.0",
        "issued_at": _iso(two_issues), "valid_at": _iso(two_issues + dt.timedelta(hours=3)), "lead_seconds": 10800,
        "site": "", "variable": "kp", "units": "1", "value": 2.333, "lower": 1.0, "upper": 4.0,
        "interval_level": 0.8,
        "provenance": {"inputs": [{"product": "noaa.kp", "latest": "2026-10-17T15:00:00Z"}],
                       "producer": "baselines.kp_persistence@0.1.0"},
    }
    assert ApiUsageDay.objects.get().endpoint == "forecast"


@pytest.mark.django_db
def test_forecast_csv_matches_the_documented_layout(client, two_issues):
    r = client.get("/api/v1/pronosticos/usach.kp.persistence.csv")
    text = _body(r)
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    assert any(line.startswith("# Licence: CC-BY-4.0") for line in text.splitlines())
    assert lines[0] == ",".join(EXCHANGE_FIELDS)
    row = next(csv.reader([lines[1]]))
    assert row[:12] == ["usach.kp.persistence", "0.1.0", _iso(two_issues),
                        _iso(two_issues + dt.timedelta(hours=3)), "10800", "", "kp", "1", "2.333", "1.0", "4.0", "0.8"]
    assert json.loads(row[12])["producer"] == "baselines.kp_persistence@0.1.0"
    assert '""inputs""' in lines[1]  # provenance as compact JSON in one quoted field


@pytest.mark.django_db
def test_forecast_range_needs_a_key_and_at_most_31_days(client, two_issues, key):
    _obj, raw = key
    since = _iso(two_issues - dt.timedelta(hours=2))
    assert client.get("/api/v1/pronosticos/usach.kp.persistence.json", {"issued_since": since}).status_code == 401
    r = client.get("/api/v1/pronosticos/usach.kp.persistence.json", {"issued_since": since}, HTTP_X_API_KEY=raw)
    assert r.status_code == 200 and len(r.json()["records"]) == 8
    r = client.get("/api/v1/pronosticos/usach.kp.persistence.json",
                   {"issued_since": "2026-08-01", "issued_until": "2026-10-01"}, HTTP_X_API_KEY=raw)
    assert r.status_code == 400
    assert client.get("/api/v1/pronosticos/usach.kp.persistence.json",
                      {"issued_until": since}, HTTP_X_API_KEY=raw).status_code == 400


@pytest.mark.django_db
def test_forecast_export_only_for_forecast_products(client):
    assert client.get("/api/v1/pronosticos/noaa.kp.json").status_code == 404
    r = client.get("/api/v1/pronosticos/usach.kp.persistence.json")
    assert r.status_code == 200 and r.json()["records"] == []
