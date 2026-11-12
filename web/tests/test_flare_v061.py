"""v0.6.1 (D-102..D-105): the flare page's new layout, "Qué miró el modelo"
(an alert frame only), the loop and replay data (whitelist, URLs rewritten
to the public nginx paths), the home page's "El Sol ahora" behind the
preview switch, degradation with the service down, and times as <time>
elements site-wide."""

import datetime as dt

import pytest
from django.template import Context, Template
from django.test import override_settings
from django.utils import translation

from core import swxkit
from web import flare, series, timefmt
from web.tests.test_flare_page import PAGE, FakeService, _iso, _publish_v2, _slot_now, _staff

LOOP = "/productos/usach.flare.nrt/datos/bucle.json"
MEDIA = "/productos/usach.flare.nrt"


@pytest.fixture
def service(monkeypatch):
    fake = FakeService()
    end = fake.end
    slots = [end - dt.timedelta(minutes=2 * i) for i in range(3, -1, -1)]
    fake.payloads["/api/loop"] = {"channel": "171", "hours": 6, "frames": [
        {"slot": _iso(t), "t_obs": _iso(t + dt.timedelta(seconds=10)), "probability": 0.05, "state": "quiet",
         "url": f"/api/render/{_iso(t)}/171.webp"} for t in slots
    ] + [
        {"slot": _iso(end), "t_obs": None, "probability": None, "state": None, "url": "http://127.0.0.1:5050/x.webp"},
        {"slot": _iso(end), "t_obs": None, "probability": None, "state": None,
         "url": f"/api/render/{_iso(end)}/171-diff.webp"},  # another channel: dropped
    ]}
    start = end - dt.timedelta(hours=5)
    fake.payloads["/api/events/3/replay"] = {
        "schema": "flare-nrt-replay/1",
        "event": {"event_id": 3, "t_start": _iso(start), "t_peak": _iso(start + dt.timedelta(minutes=4)),
                  "t_end": _iso(start + dt.timedelta(minutes=8)), "p_max": 0.912},
        "window": {"start": _iso(start - dt.timedelta(minutes=30)), "end": _iso(start + dt.timedelta(minutes=68))},
        "n_slots": 3,
        "frames": [
            {"slot": _iso(start), "t_obs": _iso(start), "probability": 0.85, "state": "onset", "status": "ok",
             "images": ["171", "gradcam-heat"]},
            {"slot": _iso(start + dt.timedelta(minutes=2)), "t_obs": None, "probability": 0.31, "state": "decay",
             "status": "ok", "images": ["171", "gradcam-heat"]},  # below threshold: no heat map shown
            {"slot": "../../etc", "images": ["171"]},  # not a slot: dropped
        ],
        "complete": False, "missing": {"171": [_iso(start + dt.timedelta(minutes=4))], "gradcam-heat": []},
        "note": "1 of 3 slots have no 171 Å image",
    }
    fake.payloads["/api/events"]["events"][0]["replay"] = True
    fake.payloads["/api/events"]["events"][1]["replay"] = False
    monkeypatch.setattr(flare.httpx, "get", fake.get)
    return fake


@pytest.fixture
def public(settings):
    settings.SWX_FLARE_PAGE_PUBLIC = True


# --- whitelist ---------------------------------------------------------------------

@pytest.mark.parametrize("name, params", [
    ("loop", {"channel": "304", "hours": 6}),
    ("loop", {"channel": "171", "hours": 25}),
    ("loop", {"channel": 171, "hours": 6}),
    ("loop", {"channel": "171", "hours": 6, "slot": "x"}),
    ("replay", {}),
    ("replay", {"event_id": 0}),
    ("replay", {"event_id": "3"}),
    ("replay", {"event_id": "3/../../status"}),
])
def test_loop_and_replay_whitelist_refuses(name, params):
    with pytest.raises(flare.NotAllowed):
        flare.service_json(name, **params)


def test_replay_event_id_goes_into_the_path(service):
    assert flare.service_json("replay", event_id=3)["frames"]
    url, params, timeout = service.calls[0]
    assert url.endswith("/api/events/3/replay") and params == {} and timeout == 2.0


# --- loop and replay data: the browser only ever sees public paths ---------------------

@pytest.mark.django_db
def test_loop_rewritten_to_public_paths(client, service):
    data = client.get(LOOP, {"channel": "171", "hours": "6"}).json()
    urls = [f["url"] for f in data["frames"]]
    assert len(urls) == 4 and all(u.startswith(MEDIA + "/render/") and u.endswith("/171.webp") for u in urls)
    assert data["frames"][0]["obs"] == data["frames"][0]["t"] + 10
    assert service.calls[0][1] == {"channel": "171", "hours": 6}
    body = client.get(LOOP).content.decode()
    assert "5050" not in body and "/api/" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("query, expected", [({"channel": "gradcam-heat"}, {"channel": "171", "hours": 6}),
                                             ({"channel": "171-diff", "hours": "99"}, {"channel": "171-diff", "hours": 6}),
                                             ({"hours": "x"}, {"channel": "171", "hours": 6})])
def test_loop_parameters_fall_back(client, service, query, expected):
    client.get(LOOP, query)
    assert service.calls[0][1] == expected


@pytest.mark.django_db
def test_replay_frames_heat_only_at_threshold(client, service):
    _publish_v2()  # threshold 0.8 from the stored result
    data = client.get(f"{MEDIA}/datos/repeticion-3.json").json()
    frames = data["frames"]
    assert [f["url"] for f in frames] == [f"{MEDIA}/replay/3/{_iso(service.end - dt.timedelta(hours=5) + dt.timedelta(minutes=m))}/171.webp"
                                         for m in (0, 2)]
    assert frames[0]["heat"].endswith("/gradcam-heat.webp") and frames[1]["heat"] is None
    assert data["complete"] is False and data["missing"] == {"171": 1, "gradcam-heat": 0} and data["n_slots"] == 3
    assert data["note"].startswith("1 of 3") and set(data["marks"]) == {"start", "peak", "end"}
    assert "5050" not in str(data) and "/api/" not in str(data)


@pytest.mark.django_db
def test_replay_unavailable_and_unknown(client, service):
    data = client.get(f"{MEDIA}/datos/repeticion-9.json").json()  # the service answers 404
    assert data["unavailable"] is True and data["frames"] == []
    assert client.get(f"{MEDIA}/datos/repeticion-0.json").status_code == 404


@pytest.mark.django_db
def test_replay_xray_is_the_store_over_the_window(client, service):
    start = service.end - dt.timedelta(hours=5)
    swxkit.publish_timeseries("noaa.goes.xray", [
        {"time_tag": start + dt.timedelta(minutes=m), "energy": "0.1-0.8nm", "flux": 1e-7 * (m + 41), "satellite": 18}
        for m in range(-40, 80)])
    data = client.get(f"{MEDIA}/datos/rayosx-3.json").json()
    first, last = data["time"][0], data["time"][-1]
    assert first == int((start - dt.timedelta(minutes=30)).timestamp())
    assert last < int((start + dt.timedelta(minutes=68)).timestamp())
    assert data["bucket_seconds"] == 60 and data["series"]["0.1-0.8nm"][0] == pytest.approx(1.1e-6)


@pytest.mark.django_db
def test_window_series_has_an_upper_bound():
    t0 = dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC)
    swxkit.publish_timeseries("noaa.goes.xray", [{"time_tag": t0 + dt.timedelta(minutes=m), "energy": "0.1-0.8nm",
                                                  "flux": 1e-6, "satellite": 18} for m in range(60)])
    data = series.window_series("noaa.goes.xray", t0 + dt.timedelta(minutes=10), t0 + dt.timedelta(minutes=20))
    assert len(data["time"]) == 10 and data["anchor"] == "window"


# --- the page -----------------------------------------------------------------------

def _order(html, markers):
    positions = [html.index(m) for m in markers]
    return positions == sorted(positions)


@pytest.mark.django_db
def test_layout_order(client, service, public):
    _publish_v2()
    html = client.get(PAGE).content.decode()
    assert _order(html, ['id="flare-img-h"', 'id="flare-cam-h"', 'class="card swx-stack', 'id="flare-hist-h"',
                         "Qué muestra", "Ficha del producto"])
    assert 'id="flare-main-frame"' in html and 'data-player data-target="flare-main-frame"' in html
    assert 'class="card swx-stack readout-below"' in html
    # The loop loads nothing by itself: its URL is only a data attribute.
    assert "/render/" not in html


@pytest.mark.django_db
def test_gradcam_last_event(client, service, public):
    _publish_v2()
    html = client.get(PAGE).content.decode()
    peak = service.end - dt.timedelta(hours=5) + dt.timedelta(minutes=4)
    cam = html.split('id="flare-cam-h"')[1].split('id="flare-time-h"')[0]
    assert f'Última alerta: <time datetime="{_iso(peak)}"' in cam
    assert f"{MEDIA}/gradcam/{_iso(peak)}.png?style=heat&amp;format=webp" in cam
    assert f"{MEDIA}/img/131.png?kind=img&amp;slot={_iso(peak)}&amp;format=webp" in cam
    assert "0,91" in cam and "cam-bar" in cam and "no una probabilidad" in cam


@pytest.mark.django_db
def test_gradcam_open_event(client, service, public):
    end = service.end
    service.payloads["/api/events"]["events"].append(
        {"event_id": 4, "t_start": _iso(end - dt.timedelta(minutes=6)), "t_pmax": _iso(end - dt.timedelta(minutes=2)),
         "t_end": _iso(end), "p_max": 0.93, "closed": False, "gradcam": None, "replay": False})
    _publish_v2(probability=0.93, alert=True, state="active", flare_detected=True)
    cam = client.get(PAGE).content.decode().split('id="flare-cam-h"')[1].split('id="flare-time-h"')[0]
    assert "Alerta en curso: imagen de mayor probabilidad" in cam
    assert f"/gradcam/{_iso(end - dt.timedelta(minutes=2))}.png?style=heat" in cam


@pytest.mark.django_db
def test_gradcam_no_event_and_never_a_quiet_frame(client, service, public):
    _publish_v2()
    service.payloads["/api/events"] = {"events": []}
    cam = client.get(PAGE).content.decode().split('id="flare-cam-h"')[1].split('id="flare-time-h"')[0]
    assert "Sin alertas recientes: el mapa se muestra durante una alerta." in cam and "gradcam/" not in cam
    # An "event" whose peak is below the threshold never gets a heat map.
    quiet = {"events": [{"event_id": 5, "t_start": _iso(service.end), "t_pmax": _iso(service.end), "p_max": 0.4,
                         "closed": True, "gradcam": f"/api/gradcam/{_iso(service.end)}.png"}]}
    assert flare.gradcam_context(quiet, 0.8, "es")["mode"] == "none"
    assert flare.gradcam_context(None, 0.8, "es")["mode"] == "unavailable"


@pytest.mark.django_db
def test_replay_button_only_with_replay(client, service, public):
    _publish_v2()
    html = client.get(PAGE).content.decode()
    rows = html.split('<tbody>')[1].split('</tbody>')[0].split("<tr>")[1:]
    assert len(rows) == 2
    assert f'data-replay="{MEDIA}/datos/repeticion-3.json"' in rows[0]
    assert f'data-replay-xray="{MEDIA}/datos/rayosx-3.json"' in rows[0]
    assert "data-replay=" not in rows[1]  # replay: false
    assert 'id="flare-replay"' in html


@pytest.mark.django_db
def test_page_en(client, service, public):
    _publish_v2()
    html = client.get("/en" + PAGE).content.decode()
    for text in ("What the model looked at", "Last alert:", "map maximum", "watch replay",
                 "Play the last 6 h (171 Å)", "Probability and X-rays", "image of "):
        assert text in html, text


@pytest.mark.django_db
def test_page_service_down(client, service, public):
    service.down = True
    _publish_v2()
    response = client.get(PAGE)
    assert response.status_code == 200
    html = response.content.decode()
    assert html.count("Sin datos del detector.") >= 2  # images, Grad-CAM and history sections
    assert client.get(LOOP).json()["unavailable"] is True


# --- home: El Sol ahora ----------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize("public_on, who, shown", [(False, "anonymous", False), (False, "staff", True),
                                                   (True, "anonymous", True)])
def test_home_block_follows_the_preview_switch(client, service, settings, public_on, who, shown):
    settings.SWX_FLARE_PAGE_PUBLIC = public_on
    _publish_v2()
    if who == "staff":
        _staff(client)
    response = client.get("/")
    html = response.content.decode()
    assert response.status_code == 200
    assert ("data-sun-now" in html) is shown
    if shown and not public_on:
        assert "private" in response["Cache-Control"]


@pytest.mark.django_db
def test_home_block_content(client, service, public):
    valid_from = _slot_now() - dt.timedelta(minutes=10)
    _publish_v2(valid_from=valid_from)
    html = client.get("/").content.decode()
    block = html.split("data-sun-now")[1].split("</section>")[0]
    slot = _iso(valid_from)
    # Only the large image is eager, with its size; the thumbnails are lazy.
    main = block.split("data-sun-main")[1].split(">")[0]
    assert f'src="{MEDIA}/img/171.png?kind=img&amp;slot={slot}&amp;format=webp&amp;size=1024"' in main
    assert 'width="1024" height="1024"' in main and "lazy" not in main
    assert block.count('loading="lazy"') == 3
    for wave in ("131", "304", "1600"):
        assert f"/img/{wave}.png?kind=img&amp;slot={slot}&amp;format=webp&amp;size=512" in block
    assert "SDO/AIA (NASA)" in block and f'href="{PAGE}"' in block and f'<time datetime="{slot}"' in block
    assert "data-fp-start" in block and "/render/" not in block
    # The home page never calls the service.
    assert service.calls == []


@pytest.mark.django_db
def test_home_block_hidden_without_a_recent_result(client, service, public):
    assert "data-sun-now" not in client.get("/").content.decode()  # nothing stored
    _publish_v2(valid_from=_slot_now() - dt.timedelta(days=4))  # older than the service's FITS
    assert "data-sun-now" not in client.get("/").content.decode()


@pytest.mark.django_db
def test_home_service_down_still_200(client, service, public):
    service.down = True
    _publish_v2()
    assert client.get("/").status_code == 200


@pytest.mark.django_db
def test_dev_media_serves_frame_paths(client, service, settings):
    service.payloads["/api/render/2026-11-11T12:02:00Z/171.webp"] = {}
    with override_settings(DEBUG=False):
        assert client.get(f"{MEDIA}/render/2026-11-11T12:02:00Z/171.webp").status_code == 404


# --- local time (D-105) --------------------------------------------------------------

@pytest.mark.parametrize("fmt, lang, text", [
    ("", "es", "12/11 21:34 UT"), ("", "en", "12 Nov 21:34 UT"), ('"t"', "es", "21:34 UT"),
    ('"ts"', "es", "21:34:05 UT"), ('"dts"', "en", "12 Nov 21:34:05 UT"),
])
def test_ut_time_filter(fmt, lang, text):
    when = dt.datetime(2026, 11, 12, 21, 34, 5, tzinfo=dt.UTC)
    key = {"": "dt", '"t"': "t", '"ts"': "ts", '"dts"': "dts"}[fmt]
    with translation.override(lang):
        out = Template("{% load swx_time %}{{ when|ut_time" + (":" + fmt if fmt else "") + " }}").render(
            Context({"when": when}))
    assert out == f'<time datetime="2026-11-12T21:34:05Z" data-ut="{key}">{text}</time>'


def test_ut_time_filter_none_and_naive():
    tpl = Template('{% load swx_time %}{{ when|ut_time|default:"—" }}')
    assert tpl.render(Context({"when": None})) == "—"
    naive = dt.datetime(2026, 1, 2, 3, 4)
    assert 'datetime="2026-01-02T03:04:00Z"' in timefmt.time_html(naive, "es")
    # A non-UTC datetime is converted, never printed in its own zone.
    chile = dt.datetime(2026, 1, 2, 0, 4, tzinfo=dt.timezone(dt.timedelta(hours=-3)))
    assert ">02/01 03:04 UT<" in timefmt.time_html(chile, "es")


@pytest.mark.django_db
def test_times_are_time_elements_site_wide(client, service, public):
    swxkit.publish_timeseries("noaa.rtsw.mag", [{"time_tag": _slot_now(), "bx_gsm": 1.0, "by_gsm": 1.0,
                                                 "bz_gsm": -1.0, "bt": 2.0, "source": "SOLAR1", "active": True}])
    swxkit.heartbeat("noaa.rtsw.mag", "ok")
    _publish_v2()
    for url in ("/", "/estado/", PAGE, "/en/estado/"):
        html = client.get(url).content.decode()
        assert '<time datetime="' in html and 'data-ut="' in html, url
        assert "localtime.js" in html and "data-tz-switch" in html, url
    # The status page used to print the server's own zone (America/Santiago) unlabelled.
    status = client.get("/estado/").content.decode()
    assert " UT</time>" in status


@pytest.mark.django_db
def test_replay_xray_empty_is_not_cached_long(client, service):
    service.down = True
    response = client.get(f"{MEDIA}/datos/rayosx-3.json")
    assert response.json()["time"] == [] and response["Cache-Control"] == "public, max-age=30"
    service.down = False
    flare.cache.clear()
    swxkit.publish_timeseries("noaa.goes.xray", [{"time_tag": service.end - dt.timedelta(hours=5), "energy": "0.1-0.8nm",
                                                  "flux": 1e-6, "satellite": 18}])
    response = client.get(f"{MEDIA}/datos/rayosx-3.json")
    assert response.json()["time"] and response["Cache-Control"] == "public, max-age=300"
