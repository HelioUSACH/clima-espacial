"""The flare detector's page (v0.6.0, D-099..D-101): the server-side reader
of flare-nrt's JSON (whitelist, 2 s timeout, 30 s cache, degradation), the
preview switch, the GOES class of each alert, the probability series, the
bbox geometry, and the page itself in ES and EN, with the service up, down,
and with a version-1 event in the store."""

import datetime as dt
import json
import threading
from pathlib import Path

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from core import swxkit
from web import flare

FIXTURES = Path(__file__).resolve().parents[2] / "modules" / "flare" / "tests" / "fixtures"
PAGE = "/productos/usach.flare.nrt/"
DATOS = "/productos/usach.flare.nrt/datos/probabilidad.json"


def _iso(t):
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slot_now():
    now = timezone.now().astimezone(dt.UTC).replace(second=0, microsecond=0)
    return now - dt.timedelta(minutes=now.minute % 2)


class FakeService:
    """flare-nrt's display endpoints, answering through a patched httpx.get."""

    def __init__(self):
        self.calls = []
        self.down = False
        end = _slot_now()
        self.end = end
        points = []
        for i in range(30, -1, -1):
            t = end - dt.timedelta(minutes=2 * i)
            if 10 <= i <= 12:  # a 3-slot hole in the list
                continue
            p = 0.9 if i in (2, 3) else (None if i == 20 else 0.05)
            points.append([_iso(t), p, "active" if p and p > 0.8 else "quiet", "ok" if p is not None else "missing"])
        self.payloads = {
            "/api/status": json.loads((FIXTURES / "status_v1_quiet.json").read_text()),
            "/api/series": {"threshold": 0.8, "lag_min": 8, "fields": ["slot", "probability", "state", "status"],
                            "points": points},
            "/api/events": {"events": [
                {"event_id": 3, "t_start": _iso(end - dt.timedelta(hours=5)),
                 "t_pmax": _iso(end - dt.timedelta(hours=5) + dt.timedelta(minutes=4)),
                 "t_end": _iso(end - dt.timedelta(hours=5) + dt.timedelta(minutes=8)), "p_max": 0.912,
                 "n_alert_frames": 5, "location": {"centroid_arcsec": [-412.3, 268.9]}, "closed": True,
                 "gradcam": "/api/gradcam/" + _iso(end - dt.timedelta(hours=5) + dt.timedelta(minutes=4)) + ".png"},
                {"event_id": 2, "t_start": _iso(end - dt.timedelta(days=2)), "t_pmax": None, "t_end": None,
                 "p_max": 0.81, "n_alert_frames": 1, "location": None, "closed": True,
                 "gradcam": "http://elsewhere.example/x.png"},
            ]},
            "/api/frames": {"frames": [
                {"slot": _iso(end - dt.timedelta(minutes=2)), "gradcam": True, "images": [131, 171, 304, 1600]},
                {"slot": _iso(end), "gradcam": False, "images": [131, 171, 304, 1600]},
            ]},
        }

    def get(self, url, params=None, timeout=None, follow_redirects=None):
        self.calls.append((url, dict(params or {}), timeout))
        if self.down:
            raise httpx.ConnectError("refused")
        path = "/" + url.split("/", 3)[3]
        if path not in self.payloads:
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json=self.payloads[path])


@pytest.fixture
def service(monkeypatch):
    fake = FakeService()
    monkeypatch.setattr(flare.httpx, "get", fake.get)
    return fake


def _publish_v2(valid_from=None, **payload):
    valid_from = valid_from or _slot_now() - dt.timedelta(minutes=10)
    body = {"probability": 0.05, "threshold": 0.8, "alert": False, "state": "quiet", "flare_detected": False,
            "event_id": None, "event_t_start": None, "location": None}
    body.update(payload)
    return swxkit.publish_event(
        "usach.flare.nrt", body, valid_from=valid_from, issued=valid_from + dt.timedelta(minutes=9),
        quality={"live_status": "live", "input_age_s": 600.0, "ensemble_std": 0.02},
        provenance={"producer": "flare-nrt@1.0.0",
                    "inputs": {w: _iso(valid_from - dt.timedelta(seconds=4)) for w in ("131", "171", "304", "1600")}},
    )


def _staff(client):
    user = get_user_model().objects.create_user("revisor", password="x" * 16, is_staff=True)
    client.force_login(user)


# --- the service reader -----------------------------------------------------------

@pytest.mark.parametrize("name, params", [
    ("health", {}),                      # an endpoint off the list
    ("image", {}),
    ("series", {"days": 15}),            # out of range
    ("series", {"days": 0}),
    ("events", {"days": "30"}),          # not an int
    ("events", {"days": True}),
    ("frames", {"hours": 1, "start": 1}),  # a parameter off the list
    ("status", {"days": 1}),
])
def test_whitelist_refuses(name, params):
    with pytest.raises(flare.NotAllowed):
        flare.service_json(name, **params)


def test_fetch_uses_2s_timeout_and_whitelisted_url(service, settings):
    settings.SWX_FLARE_NRT_URL = "http://127.0.0.1:5050"
    assert flare.service_json("events", days=30)["events"]
    assert service.calls == [("http://127.0.0.1:5050/api/events", {"days": 30}, 2.0)]


def test_cached_30s_failures_included(service):
    flare.service_json("status")
    flare.service_json("status")
    assert len(service.calls) == 1
    service.down = True
    flare.cache.clear()
    assert flare.service_json("frames", hours=1) is None
    assert flare.service_json("frames", hours=1) is None
    assert len(service.calls) == 2  # the failure is cached too: one wait per 30 s, not per visitor


def test_timeout_is_no_data(monkeypatch):
    def slow(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")
    monkeypatch.setattr(flare.httpx, "get", slow)
    assert flare.service_json("status") is None


def test_unexpected_shape_is_no_data(service):
    service.payloads["/api/events"] = {"events": "nope"}
    assert flare.service_json("events", days=30) is None


def test_a_second_thread_never_waits(service):
    """While one thread is fetching a key, another gets None at once."""
    key = "swx:flare-nrt:/api/status"
    lock = flare._lock_for(key)
    lock.acquire()
    try:
        result = []
        t = threading.Thread(target=lambda: result.append(flare.service_json("status")))
        t.start()
        t.join(timeout=1)
        assert not t.is_alive() and result == [None]
        assert service.calls == []
    finally:
        lock.release()


# --- geometry, GOES class, series -----------------------------------------------

def test_bbox_percent_north_up_and_clipped():
    # 4.8″/px on 512 px, disk centre at the middle; +x right, +y up.
    box = flare.bbox_percent({"bbox_arcsec": [0, 0, 48, 96]})
    assert box == {"left": "50.00", "top": "46.09", "width": "1.95", "height": "3.91"}
    clipped = flare.bbox_percent({"bbox_arcsec": [1200, -100, 1400, 100]})
    assert clipped["left"] == "98.83" and float(clipped["left"]) + float(clipped["width"]) == pytest.approx(100)
    assert flare.bbox_percent({"bbox_arcsec": [2000, 0, 2100, 10]}) is None
    assert flare.bbox_percent(None) is None and flare.bbox_percent({"bbox_arcsec": [1, 2]}) is None


@pytest.mark.django_db
def test_goes_class_is_max_long_flux_in_window():
    t0 = dt.datetime(2026, 10, 28, 16, 0, tzinfo=dt.UTC)
    rows = []
    for i in range(60):
        t = t0 + dt.timedelta(minutes=i)
        long_flux = 2.4e-5 if i == 25 else 3e-7
        rows.append({"time_tag": t, "energy": "0.1-0.8nm", "flux": long_flux, "satellite": 18})
        rows.append({"time_tag": t, "energy": "0.05-0.4nm", "flux": 9e-4, "satellite": 18})  # never counted
    rows.append({"time_tag": t0 + dt.timedelta(minutes=61), "energy": "0.1-0.8nm", "flux": -1e5, "satellite": 18})
    swxkit.publish_timeseries("noaa.goes.xray", rows)
    windows = [
        (t0 + dt.timedelta(minutes=10), t0 + dt.timedelta(minutes=20)),  # before the peak
        (t0 + dt.timedelta(minutes=10), t0 + dt.timedelta(minutes=30)),  # contains it
        (t0 + dt.timedelta(hours=5), t0 + dt.timedelta(hours=6)),        # nothing stored
    ]
    assert flare.goes_classes(windows) == ["B3.0", "M2.4", None]
    assert flare.goes_classes([]) == []


@pytest.mark.django_db
def test_history_class_window_reaches_10_min_after_end(service):
    end = service.end - dt.timedelta(hours=5) + dt.timedelta(minutes=8)
    swxkit.publish_timeseries("noaa.goes.xray", [
        {"time_tag": end + dt.timedelta(minutes=9), "energy": "0.1-0.8nm", "flux": 1.2e-6, "satellite": 18}])
    rows = flare.history_rows(flare.service_json("events", days=30), "es")
    assert [r["goes_class"] for r in rows] == ["C1.2", "—"]  # newest first
    # v0.6.2 (D-108): no parentheses in a table cell.
    assert rows[0]["p_max"] == "0,91" and rows[0]["n_frames"] == 5 and rows[0]["location"] == "−412″, 269″"
    assert rows[0]["heat"].startswith("/productos/usach.flare.nrt/gradcam/") and "Z.png?style=heat" in rows[0]["heat"]
    # Anything but the service's own /api/gradcam/<ISO>.png is dropped (and
    # this event has no t_pmax to fall back on).
    assert rows[1]["heat"] is None and rows[1]["cam_mode"] == "none"
    assert rows[1]["peak"] == "—"


@pytest.mark.django_db
def test_probability_series_gaps_nulls_and_spans(service):
    data = flare.probability_series("6h")
    times, values = data["time"], data["series"]["probability"]
    assert all(b - a == 120 for a, b in zip(times, times[1:], strict=False))  # holes became null slots
    assert values.count(None) == 4  # the 3 missing slots + the one without a result
    assert data["threshold"] == 0.8
    assert data["spans"] == [[times[-4], times[-2]]]  # two consecutive alert slots, merged
    assert data["unavailable"] is False
    service.down = True
    flare.cache.clear()
    empty = flare.probability_series("1d")
    assert empty["time"] == [] and empty["unavailable"] is True


# --- the data endpoint ------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize("public", [False, True])
def test_datos_served_in_both_modes(client, service, settings, public):
    settings.SWX_FLARE_PAGE_PUBLIC = public
    response = client.get(DATOS, {"range": "6h"})
    assert response.status_code == 200
    assert response["Cache-Control"] == "public, max-age=30"
    assert response.json()["series"]["probability"]
    assert service.calls[0][1] == {"days": 1}


@pytest.mark.django_db
def test_datos_whitelist_and_range_fallback(client, service):
    assert client.get("/productos/usach.flare.nrt/datos/status.json").status_code == 404
    client.get(DATOS, {"range": "1y"})
    assert service.calls[0][1] == {"days": 1}  # back to the default 1 día


@pytest.mark.django_db
def test_datos_service_down_is_empty_not_error(client, service):
    service.down = True
    response = client.get(DATOS)
    assert response.status_code == 200 and response.json()["unavailable"] is True


@pytest.mark.django_db
def test_dev_image_route_is_debug_only(client, service):
    with override_settings(DEBUG=False):
        assert client.get("/productos/usach.flare.nrt/img/131.png").status_code == 404


# --- the preview switch (D-099) -----------------------------------------------------

def _is_new_page(response):
    return b"flare-page" in response.content


@pytest.mark.django_db
@pytest.mark.parametrize("public, who, new_page", [
    (False, "anonymous", False),
    (False, "user", False),     # logged in, not staff
    (False, "staff", True),
    (True, "anonymous", True),
    (True, "staff", True),
])
def test_preview_switch(client, service, settings, public, who, new_page):
    settings.SWX_FLARE_PAGE_PUBLIC = public
    _publish_v2()
    if who == "staff":
        _staff(client)
    elif who == "user":
        client.force_login(get_user_model().objects.create_user("visita", password="y" * 16))
    response = client.get(PAGE)
    assert response.status_code == 200
    assert _is_new_page(response) is new_page
    if new_page and not public:
        assert "private" in response["Cache-Control"]  # a staff preview is never shared-cached


@pytest.mark.django_db
@pytest.mark.parametrize("query", ["?preview=1", "?public=1", "?SWX_FLARE_PAGE_PUBLIC=1", "?staff=1"])
def test_no_url_parameter_forces_the_preview(client, service, settings, query):
    settings.SWX_FLARE_PAGE_PUBLIC = False
    _publish_v2()
    assert not _is_new_page(client.get(PAGE + query))


@pytest.mark.django_db
def test_catalogue_links_the_card_only_when_visible(client, service, settings):
    _publish_v2()
    settings.SWX_FLARE_PAGE_PUBLIC = False
    assert b'href="/productos/usach.flare.nrt/"' not in client.get("/productos/").content
    settings.SWX_FLARE_PAGE_PUBLIC = True
    assert b'href="/productos/usach.flare.nrt/"' in client.get("/productos/").content


# --- the page ---------------------------------------------------------------------

@pytest.fixture
def public(settings):
    settings.SWX_FLARE_PAGE_PUBLIC = True


@pytest.mark.django_db
def test_page_es_with_open_event(client, service, public):
    start = _slot_now() - dt.timedelta(minutes=16)
    _publish_v2(probability=0.9312, alert=True, state="active", flare_detected=True, event_id=7,
                event_t_start=_iso(start),
                location={"centroid_arcsec": [-412.3, 268.9], "bbox_arcsec": [-460.8, 230.4, -364.8, 307.2],
                          "confidence": "experimental"})
    html = client.get(PAGE).content.decode()
    assert "Flare en curso" in html
    assert "0,93" in html and "umbral" in html and "0,80" in html
    assert "imagen de " in html and "hace " in html
    assert f'Alerta desde <time datetime="{_iso(start)}" data-ut="t">{start:%H:%M} UT</time>' in html
    assert "(−412″, 269″)" in html and "experimental" in html
    assert 'class="flare-box" style="left:' in html and "," not in html.split('class="flare-box" style="')[1][:60]
    # Images: the stored slot, through the public (nginx) paths, never the service's address.
    assert "/productos/usach.flare.nrt/img/171.png?kind=img&amp;slot=" in html
    assert "/productos/usach.flare.nrt/img/1600.png?kind=diff&amp;slot=" in html
    assert "/productos/usach.flare.nrt/gradcam/" in html
    assert "5050" not in html and "/api/" not in html.replace("/api/v1/", "")
    assert "Grad-CAM" in html and "Sin alertas" not in html
    assert "Ficha del producto" in html and "aia131_rise" not in html  # plain words, not code names
    assert "98 %" in html and "14″" in html  # the honest summary


@pytest.mark.django_db
def test_page_en(client, service, public):
    _publish_v2(state="decay", flare_detected=True, event_t_start=_iso(_slot_now() - dt.timedelta(minutes=20)))
    html = client.get("/en" + PAGE).content.decode()
    assert "Decay" in html and "threshold" in html
    assert "image of " in html and " min ago" in html
    assert "Alerts in the last 30 days" in html and "Product fact sheet" in html
    assert "Events" in html and "Choose a row to see the event." in html
    assert "0.05" in html
    # "72 %" in a {% trans %} literal is looked up as "72 %%" (Django doubles it).
    assert "It is a beta product." in html and "98 %" in html and "Es un producto" not in html


@pytest.mark.django_db
def test_page_quiet_no_alerts(client, service, public):
    service.payloads["/api/events"] = {"events": []}
    _publish_v2()
    html = client.get(PAGE).content.decode()
    assert "Calma" in html and "Sin alertas en los últimos 30 días." in html
    assert "flare-box" not in html and "Alerta desde" not in html


@pytest.mark.django_db
def test_page_service_down_still_renders(client, service, public):
    service.down = True
    _publish_v2()
    response = client.get(PAGE)
    assert response.status_code == 200
    html = response.content.decode()
    assert "Sin datos del detector" in html
    assert "0,05" in html  # the last result stored by the portal still shows
    assert "flare-frame" not in html  # no image tags pointing at a dead service


@pytest.mark.django_db
def test_page_with_no_event_and_no_service(client, service, public):
    service.down = True
    swxkit.heartbeat("usach.flare.nrt", "error", detail="unreachable")
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "en preparación" in response.content.decode()


@pytest.mark.django_db
def test_version_1_event_still_renders(client, service, public):
    """An event stored before D-098: probability, flare_detected, location
    only; valid_from = issued; no provenance."""
    issued = timezone.now() - dt.timedelta(minutes=5)
    swxkit.publish_event("usach.flare.nrt", {"probability": 0.0912, "flare_detected": False, "location": None},
                         valid_from=issued, issued=issued, quality={"producer_live_status": "live"})
    response = client.get(PAGE)
    assert response.status_code == 200
    html = response.content.decode()
    assert "0,09" in html and "resultado de " in html
    assert "umbral" not in html.split('class="now"')[1].split("</div>")[1]  # no threshold in the headline
    assert "flare-state" not in html  # no state badge without a state
    tile = html.split('data-tile data-wave="171" data-kind="img"', 1)[1].split("</figure>", 1)[0]
    assert 'src="/productos/usach.flare.nrt/img/171.png?kind=img&amp;format=webp"' in tile  # latest


@pytest.mark.django_db
def test_old_page_still_renders_a_v2_event_for_the_public(client, service, settings):
    settings.SWX_FLARE_PAGE_PUBLIC = False
    _publish_v2()
    response = client.get(PAGE)
    assert response.status_code == 200 and "Último evento" in response.content.decode()


@pytest.mark.django_db
def test_home_and_status_unchanged(client, service, public):
    _publish_v2()
    assert client.get("/").status_code == 200
    status = client.get("/estado/").content.decode()
    assert "Detección de fulguraciones (NRT)" in status


def test_templates_pass_the_check():
    call_command("check_templates")
