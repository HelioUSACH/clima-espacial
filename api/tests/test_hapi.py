"""The HAPI server (D-088): response shapes, subsetting, errors, access."""

import copy
import datetime as dt
import json

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import set_script_prefix

from api import export
from api.models import ApiUsageDay
from core.manifests.loader import discover_manifests


def _iso(t):
    return export.fmt_time(t)


def _get(client, path, **params):
    return client.get("/hapi/" + path, params)


def _body(response):
    return b"".join(response.streaming_content).decode() if response.streaming else response.content.decode()


def _status(response):
    return json.loads(_body(response))["status"]["code"]


@pytest.mark.django_db
def test_about_and_capabilities(client, recent_mag):
    about = _get(client, "about").json()
    assert about["HAPI"] == "3.3" and about["status"] == {"code": 1200, "message": "OK"}
    assert about["id"] and about["title"] and about["contact"]
    assert "3.3.1" in about["x_specification"]
    assert about["dataTest"]["query"]["dataset"] == "noaa.rtsw.mag"
    assert about["dataTest"]["query"]["parameters"] == "bt,bx_gsm,by_gsm,bz_gsm"
    caps = _get(client, "capabilities")
    assert caps.json()["outputFormats"] == ["csv", "json"]
    assert caps["Access-Control-Allow-Origin"] == "*"
    assert caps["Content-Type"].startswith("application/json")
    assert caps.has_header("Last-Modified")


@pytest.mark.django_db
def test_catalog_lists_only_exportable_datasets_with_data(client, recent_mag):
    catalog = _get(client, "catalog").json()
    assert catalog["catalog"] == [{"id": "noaa.rtsw.mag", "title": "Interplanetary magnetic field (L1)"}]
    full = _get(client, "catalog", depth="all").json()
    assert full["catalog"][0]["info"]["parameters"][0]["name"] == "Time"
    assert "status" not in full["catalog"][0]["info"]
    assert _status(_get(client, "catalog", depth="deep")) == 1413


@pytest.mark.django_db
def test_info_shape(client, recent_mag):
    info = _get(client, "info", dataset="noaa.rtsw.mag").json()
    assert info["parameters"][0] == {"name": "Time", "type": "isotime", "units": "UTC", "fill": None, "length": 20}
    bz = info["parameters"][4]
    assert bz["name"] == "bz_gsm" and bz["type"] == "double" and bz["units"] == "nT" and bz["fill"] == "-1.0E31"
    assert info["startDate"] == _iso(recent_mag - dt.timedelta(minutes=2 * 1440 - 1))
    assert info["stopDate"] == _iso(recent_mag)
    assert info["cadence"] == "PT1M" and info["timeStampLocation"] == "begin"
    assert info["maxRequestDuration"] == "P31D"
    day = recent_mag.replace(hour=0, minute=0)
    assert (info["sampleStartDate"], info["sampleStopDate"]) == (_iso(day - dt.timedelta(days=1)), _iso(day))
    assert info["resourceURL"] == "http://testserver/productos/noaa.rtsw.mag/"
    assert info["x_relay"] is True
    assert info["x_provider_url"] == "https://www.spaceweather.gov/products/solar-wind"
    assert "may be withdrawn" in info["x_relay_note"]
    assert info["x_license"] == "Public domain (NOAA)"
    # Old (HAPI 2) name for the dataset, and subsetting.
    sub = _get(client, "info", id="noaa.rtsw.mag", parameters="bx_gsm,bz_gsm").json()
    assert [p["name"] for p in sub["parameters"]] == ["Time", "bx_gsm", "bz_gsm"]


@pytest.mark.django_db
def test_data_csv_and_json_with_subsetting(client, recent_mag, key):
    _obj, raw = key
    start, stop = recent_mag - dt.timedelta(minutes=2), recent_mag + dt.timedelta(minutes=1)
    r = _get(client, "data", dataset="noaa.rtsw.mag", start=_iso(start), stop=_iso(stop), parameters="bz_gsm",
             api_key=raw)
    assert r.status_code == 200 and r["Content-Type"].startswith("text/csv")
    lines = _body(r).splitlines()
    assert len(lines) == 3 and lines[0].startswith(_iso(start) + ",") and len(lines[0].split(",")) == 2
    # time.min/time.max and id are the same request (HAPI 2 names).
    r2 = _get(client, "data", id="noaa.rtsw.mag", **{"time.min": _iso(start), "time.max": _iso(stop)},
              parameters="bz_gsm", api_key=raw)
    assert _body(r2).splitlines() == lines
    j = _get(client, "data", dataset="noaa.rtsw.mag", start=_iso(start), stop=_iso(stop), format="json",
             api_key=raw)
    payload = json.loads(_body(j))
    assert payload["format"] == "json" and payload["status"]["code"] == 1200
    assert len(payload["data"]) == 3 and len(payload["data"][0]) == 5
    assert payload["data"][-1][0] == _iso(recent_mag)
    h = _get(client, "data", dataset="noaa.rtsw.mag", start=_iso(start), stop=_iso(stop), include="header",
             api_key=raw)
    text = _body(h)
    header = json.loads("".join(line[1:] for line in text.splitlines() if line.startswith("#")))
    assert header["format"] == "csv" and [p["name"] for p in header["parameters"]][:2] == ["Time", "bt"]
    assert len([line for line in text.splitlines() if not line.startswith("#")]) == 3


@pytest.mark.django_db
def test_no_data_in_range_is_1201(client, recent_mag, key):
    _obj, raw = key
    t = recent_mag - dt.timedelta(days=10)
    r = _get(client, "data", dataset="noaa.rtsw.mag", start=_iso(t), stop=_iso(t + dt.timedelta(hours=1)),
             format="json", api_key=raw)
    assert r.status_code == 200 and json.loads(_body(r))["status"]["code"] == 1201
    r = _get(client, "data", dataset="noaa.rtsw.mag", start=_iso(t), stop=_iso(t + dt.timedelta(hours=1)),
             api_key=raw)
    assert r.status_code == 200 and _body(r) == ""


@pytest.mark.django_db
@pytest.mark.parametrize("params,http,code", [
    ({"dataset": "noaa.rtsw.mag", "start": "2026-10-01", "stop": "2026-10-02", "foo": "1"}, 400, 1401),
    ({"start": "2026-10-01", "stop": "2026-10-02"}, 400, 1400),
    ({"dataset": "noaa.rtsw.mag", "start": "nope", "stop": "2026-10-02"}, 400, 1402),
    ({"dataset": "noaa.rtsw.mag", "start": "2026-10-01"}, 400, 1403),
    ({"dataset": "noaa.rtsw.mag", "start": "2026-10-02", "stop": "2026-10-01"}, 400, 1404),
    ({"dataset": "kyoto.dst", "start": "2026-10-01", "stop": "2026-10-02"}, 404, 1406),
    ({"dataset": "noaa.rtsw.mag", "start": "2026-10-01", "stop": "2026-10-02", "parameters": "speed"}, 404, 1407),
    ({"dataset": "noaa.rtsw.mag", "start": "2026-08-01", "stop": "2026-10-02"}, 400, 1408),
    ({"dataset": "noaa.rtsw.mag", "start": "2026-10-01", "stop": "2026-10-02", "format": "binary"}, 400, 1409),
    ({"dataset": "noaa.rtsw.mag", "start": "2026-10-01", "stop": "2026-10-02", "include": "all"}, 400, 1410),
    ({"dataset": "noaa.rtsw.mag", "start": "2026-10-01", "stop": "2026-10-02", "parameters": "bz_gsm,bt"}, 400, 1411),
])
def test_data_error_codes(client, recent_mag, params, http, code):
    r = _get(client, "data", **params)
    assert r.status_code == http
    body = r.json()
    assert body["HAPI"] == "3.3" and body["status"]["code"] == code
    assert body["status"]["message"].startswith(f"HAPI error {code}: ")
    assert f"HAPI {code}" in r.reason_phrase
    assert r["Access-Control-Allow-Origin"] == "*"


@pytest.mark.django_db
def test_info_errors(client, recent_mag):
    assert _get(client, "info", dataset="kyoto.ae").status_code == 404
    assert _status(_get(client, "info", dataset="kyoto.ae")) == 1406
    assert _status(_get(client, "info", dataset="noaa.rtsw.mag", parameters="bz_gsm,bz_gsm")) == 1411
    assert _status(_get(client, "info", dataset="noaa.rtsw.mag", resolve_references="maybe")) == 1412
    assert _status(_get(client, "info", dataset="noaa.rtsw.mag", start="x")) == 1401
    r = client.get("/hapi/nosuch")
    assert r.status_code == 400 and r.json()["status"]["code"] == 1400


@pytest.mark.django_db
def test_keyless_preview_and_401(client, recent_mag):
    stop = recent_mag + dt.timedelta(minutes=1)
    ok = _get(client, "data", dataset="noaa.rtsw.mag", start=_iso(stop - dt.timedelta(hours=24)), stop=_iso(stop))
    assert ok.status_code == 200 and len(_body(ok).splitlines()) == 1440
    usage = ApiUsageDay.objects.get()
    assert (usage.key_id, usage.endpoint, usage.requests, usage.rows) == (None, "hapi", 1, 1440)
    refused = _get(client, "data", dataset="noaa.rtsw.mag", start=_iso(stop - dt.timedelta(hours=25)),
                   stop=_iso(stop))
    assert refused.status_code == 401
    body = refused.json()
    assert body["status"]["code"] == 1400 and "http://testserver/datos-api/" in body["status"]["message"]
    bad_key = _get(client, "data", dataset="noaa.rtsw.mag", start=_iso(stop - dt.timedelta(hours=1)),
                   stop=_iso(stop), api_key="swx_wrong")
    assert bad_key.status_code == 401


@pytest.mark.django_db
def test_quota_exceeded_is_429_with_retry_after(client, recent_mag, key, settings):
    settings.SWX_API_DAILY_ROWS = 100
    _obj, raw = key
    stop = recent_mag + dt.timedelta(minutes=1)
    r = client.get("/hapi/data", {"dataset": "noaa.rtsw.mag", "start": _iso(stop - dt.timedelta(hours=2)),
                                  "stop": _iso(stop)}, HTTP_X_API_KEY=raw)
    assert r.status_code == 429 and r.json()["status"]["code"] == 1400 and int(r["Retry-After"]) > 0


@pytest.mark.django_db
def test_key_usage_is_counted(client, recent_mag, key):
    obj, raw = key
    stop = recent_mag + dt.timedelta(minutes=1)
    client.get("/hapi/data", {"dataset": "noaa.rtsw.mag", "start": _iso(stop - dt.timedelta(days=2)),
                              "stop": _iso(stop)}, HTTP_X_API_KEY=raw)
    usage = ApiUsageDay.objects.get(key=obj)
    assert (usage.endpoint, usage.requests, usage.rows) == ("hapi", 1, 2 * 1440)


@pytest.mark.django_db
def test_goes_data_has_one_column_per_channel(client, key):
    from api.tests.test_export import _publish_fixture
    from modules.swpc import adapters

    records = _publish_fixture("noaa.goes.protons", "integral_protons_1_day.json", adapters.parse_goes_protons)
    _obj, raw = key
    times = sorted({r["time_tag"] for r in records})
    stop = (export.parse_time(times[-1]) + dt.timedelta(minutes=5))
    r = _get(client, "data", dataset="noaa.goes.protons", start=times[0], stop=_iso(stop),
             parameters="flux_ge10MeV,satellite", api_key=raw)
    lines = _body(r).splitlines()
    assert len(lines) == len(times)
    expected = next(x["flux"] for x in records if x["time_tag"] == times[0] and x["energy"] == ">=10 MeV")
    assert lines[0] == f"{times[0]},{expected!r},18"


@pytest.mark.django_db
def test_withdrawn_dataset_disappears_everywhere(client, recent_mag, key, monkeypatch):
    _obj, raw = key
    manifests = copy.deepcopy(discover_manifests())
    manifests["noaa.rtsw.mag"]["exchange"]["hapi"] = False
    monkeypatch.setattr(export, "discover_manifests", lambda: manifests)
    stop = recent_mag + dt.timedelta(minutes=1)
    window = {"start": _iso(stop - dt.timedelta(hours=1)), "stop": _iso(stop)}
    assert "noaa.rtsw.mag" not in json.dumps(_get(client, "catalog").json())
    assert _status(_get(client, "info", dataset="noaa.rtsw.mag")) == 1406
    assert _status(_get(client, "data", dataset="noaa.rtsw.mag", api_key=raw, **window)) == 1406
    assert client.get("/api/v1/datos/noaa.rtsw.mag.csv", window, HTTP_X_API_KEY=raw).status_code == 404


@pytest.mark.django_db
def test_own_product_is_served_with_no_relay_flag(client, own_product, key):
    manifest, end = own_product
    _obj, raw = key
    assert "usach.test.own" in [d["id"] for d in _get(client, "catalog").json()["catalog"]]
    info = _get(client, "info", dataset="usach.test.own").json()
    assert info["x_relay"] is False and "x_relay_note" not in info
    assert info["licenseURL"] == "https://spdx.org/licenses/CC-BY-4.0.html"
    assert info["datasetCitation"] == "HelioUSACH (2026), test product, doi:10.0000/test"
    assert [p["units"] for p in info["parameters"][1:]] == ["nT/min", "dimensionless"]
    r = _get(client, "data", dataset="usach.test.own", start=_iso(end), stop=_iso(end + dt.timedelta(minutes=1)),
             api_key=raw)
    assert _body(r) == f"{_iso(end)},0.0,0.25\n"


def test_options_preflight(client):
    r = client.options("/hapi/data")
    assert r.status_code == 204 and "X-API-Key" in r["Access-Control-Allow-Headers"]


# D-093: /hapi and /hapi/ redirect to the data-access page's HAPI section.
@pytest.mark.parametrize("path", ["/hapi", "/hapi/"])
@pytest.mark.parametrize("headers, target", [
    ({}, "/datos-api/#hapi"),
    ({"HTTP_ACCEPT_LANGUAGE": "es-CL,es;q=0.9"}, "/datos-api/#hapi"),
    ({"HTTP_ACCEPT_LANGUAGE": "en-US,en;q=0.9"}, "/en/datos-api/#hapi"),
    ({"HTTP_ACCEPT_LANGUAGE": "de-DE,de;q=0.9"}, "/datos-api/#hapi"),
])
def test_landing_redirects_by_accept_language(client, path, headers, target):
    r = client.get(path, {"x": "1"}, **headers)
    assert r.status_code == 302 and r["Location"] == target  # query string dropped
    assert r["Access-Control-Allow-Origin"] == "*"
    assert "Accept-Language" in r["Vary"]


def test_landing_language_cookie_beats_accept_language(client):
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "es"
    assert client.get("/hapi", HTTP_ACCEPT_LANGUAGE="en")["Location"] == "/datos-api/#hapi"
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
    assert client.get("/hapi", HTTP_ACCEPT_LANGUAGE="es")["Location"] == "/en/datos-api/#hapi"


@override_settings(FORCE_SCRIPT_NAME="/clima-espacial")
def test_landing_redirect_keeps_the_script_prefix(client):
    # Production runs under /clima-espacial (D-005); the test client doesn't
    # set the script prefix itself (web/tests/test_language_switch.py).
    set_script_prefix("/clima-espacial")
    try:
        r = client.get("/hapi", HTTP_ACCEPT_LANGUAGE="en")
        assert r["Location"] == "/clima-espacial/en/datos-api/#hapi"
    finally:
        set_script_prefix("/")


def test_landing_options_and_methods(client):
    assert client.options("/hapi")["Access-Control-Allow-Origin"] == "*"
    assert client.head("/hapi/").status_code == 302
    assert client.post("/hapi").status_code == 405


@pytest.mark.django_db
def test_landing_target_has_the_hapi_section(client, recent_mag):
    for lang_header, page, heading, version in (
            ("es", "/datos-api/", "Servidor HAPI", "HAPI 3.3, especificación 3.3.1"),
            ("en", "/en/datos-api/", "HAPI server", "HAPI 3.3, specification 3.3.1")):
        r = client.get("/hapi", HTTP_ACCEPT_LANGUAGE=lang_header, follow=True)
        assert r.redirect_chain == [(page + "#hapi", 302)]
        html = r.content.decode()
        assert 'id="hapi"' in html and 'href="#hapi"' in html
        assert 'href="/hapi/about"' in html and 'href="/hapi/capabilities"' in html and 'href="/hapi/catalog"' in html
        assert 'href="https://hapi-server.org/"' in html
        assert heading in html and version in html  # translated with the rest of the page
        # The existing table's ids link to HAPI info; no second dataset table.
        assert 'href="/hapi/info?dataset=noaa.rtsw.mag"' in html
        assert html.count("<table") == 1
        # Not in HAPI's catalog until it has data: plain text, no info link.
        assert "<code>noaa.kp</code>" in html and "info?dataset=noaa.kp" not in html
