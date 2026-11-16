"""v0.6.3 (D-113..D-118), the maintainer's review of v0.6.2 with flare-nrt
1.3.0: a Grad-CAM map on every replay frame with a result, weighted by its
probability (CAM_FADE_FLOOR); the "alerta" tag; the event's experimental
location drawn from flare-nrt's own geometry (replay frames from t_start to
t_end, the event panel, the live 131/171 tiles and the 6-h loop while an
event is open), in words ("S12 W68") in the table and the panel, dashed and
"aproximada" for a robust location. What the browser does with them (the
replay starting at its first frame with and without reduced motion, the
marks that never overlap, the box or circle, "Mostrar ubicación") is
checked by .github/scripts/screenshots.py in CI."""

import datetime as dt
import json
import math
import re

import pytest

from web import flare
from web.tests.test_flare_page import FakeService, _iso, _publish_v2
from web.tests.test_flare_v062 import _page, _panel, _tbody

GEOMETRY = {"size_px": 512, "disk_center_px": [256.0, 256.0], "disk_radius_px": 196.667,
            "arcsec_per_px": [4.8, 4.8], "rsun_arcsec": 944.004}
FIRST_RULE = {"centroid_arcsec": [-732.7, -212.9], "bbox_arcsec": [-744.0, -225.6, -720.0, -201.6],
              "area_px": 22, "area_arcsec2": 506.9, "on_limb": False, "method": "diff131_rate_brightest_region",
              "confidence": "experimental"}
ROBUST = {"centroid_arcsec": [-766.4, -318.8], "bbox_arcsec": [-782.4, -340.8, -753.6, -297.6],
          "area_px": 44, "area_arcsec2": 1013.8, "on_limb": False,
          "method": "diff131_rate_brightest_region_robust", "confidence": "experimental"}


@pytest.fixture
def service(monkeypatch):
    fake = FakeService()
    events = fake.payloads["/api/events"]["events"]
    events[0]["replay"] = True   # event 3, 5 h ago
    events[1]["replay"] = False
    monkeypatch.setattr(flare.httpx, "get", fake.get)
    return fake


@pytest.fixture
def public(settings):
    settings.SWX_FLARE_PAGE_PUBLIC = True


def _replay(fake, *, event_id=3, new=True, location=FIRST_RULE, peak_location=None, frame_geometry=None):
    """A replay.json for event 3 (5 h ago, start/peak/end +0/+4/+8 min) in
    1.3.0's shape (`new`) or 1.2.0's."""
    e = next(x for x in fake.payloads["/api/events"]["events"] if x["event_id"] == event_id)
    start, peak, end = (flare.parse_time(e[k]) for k in ("t_start", "t_pmax", "t_end"))
    frames = []
    t = start - dt.timedelta(minutes=10)
    while t <= end + dt.timedelta(minutes=10):
        minutes = (t - peak).total_seconds() / 60
        p = None if minutes == -8 else round(0.1 + 0.85 * math.exp(-(minutes / 5) ** 2), 4)
        alert = p is not None and p >= 0.8
        frame = {"slot": _iso(t), "probability": p, "state": "active" if alert else "quiet",
                 "images": ["171", "131"] + (["gradcam-heat"] if (new and p is not None) or alert else [])}
        if new:
            frame.update(alert=alert if p is not None else None, cam_peak=0.9 if p is not None else None,
                         location=location if alert else None, geometry=frame_geometry or GEOMETRY)
        frames.append(frame)
        t += dt.timedelta(minutes=2)
    replay = {"schema": "flare-nrt-replay/1",
              "event": {"event_id": event_id, "t_start": e["t_start"], "t_peak": e["t_pmax"], "t_end": e["t_end"],
                        "location": location},
              "window": {"start": frames[0]["slot"], "end": frames[-1]["slot"]}, "n_slots": len(frames),
              "frames": frames, "complete": True,
              "peak": {"slot": e["t_pmax"], "probability": 0.95, "gradcam_peak": 0.97,
                       "images": ["131", "171", "gradcam-heat"]},
              "missing": {"171": [], "gradcam-heat": [], "131": [], "peak": []}}
    if new:
        replay.update(threshold=0.8, geometry=GEOMETRY)
        replay["peak"].update(location=peak_location or location, geometry=GEOMETRY)
    fake.payloads[f"/api/events/{event_id}/replay"] = replay
    return start, peak, end


# --- D-115: a map on every frame, weighted by probability ---------------------------

def test_fade_floor_is_one_named_constant():
    assert flare.CAM_FADE_FLOOR == 0.30


@pytest.mark.django_db
def test_every_frame_with_a_result_has_its_map(service):
    _replay(service)
    data = flare.replay_payload(3, 0.8)
    assert data["cam_floor"] == 0.30 and data["threshold"] == 0.8
    with_p = [f for f in data["frames"] if f["p"] is not None]
    assert with_p and all(f["heat"] and f["heat"].endswith("/gradcam-heat.webp") for f in with_p)
    # A slot without a result: no map.
    assert [f["heat"] for f in data["frames"] if f["p"] is None] == [None]
    assert [f["alert"] for f in data["frames"]] == [f["p"] is not None and f["p"] >= 0.8 for f in data["frames"]]
    assert any(f["alert"] for f in data["frames"]) and not all(f["alert"] for f in with_p)


@pytest.mark.django_db
def test_the_replays_own_threshold_wins(service):
    _replay(service)
    service.payloads["/api/events/3/replay"]["threshold"] = 0.75
    assert flare.replay_payload(3, 0.8)["threshold"] == 0.75


@pytest.mark.django_db
def test_old_replay_maps_at_or_above_the_threshold_only(service):
    _replay(service, new=False)
    data = flare.replay_payload(3, 0.8)
    assert all((f["heat"] is not None) == (f["p"] is not None and f["p"] >= 0.8) for f in data["frames"])
    assert any(f["heat"] for f in data["frames"])
    # No geometry in a 1.2.0 replay: nothing drawn, never a guessed one.
    assert all(f["loc"] is None for f in data["frames"]) and data["geometry"] is None
    assert data["location"]["shape"] is None and data["location"]["text"]


@pytest.mark.django_db
def test_fade_note_under_the_player(client, service, public):
    _replay(service)
    _publish_v2()
    html = _page(client)
    assert "El mapa aparece a medida que sube la probabilidad: transparente bajo 0,30, completo en el umbral." in html
    html = client.get("/en" + "/productos/usach.flare.nrt/").content.decode()
    assert "The map appears as the probability rises: transparent below 0.30, full at the threshold." in html


@pytest.mark.django_db
def test_player_strings(client, service, public):
    _publish_v2()
    html = _page(client)
    strings = json.loads(html.split('id="fp-i18n" type="application/json">', 1)[1].split("</script>", 1)[0])
    assert strings["alert"] == "alerta"
    assert strings["loc_estimated"] == "ubicación estimada (experimental)"
    assert strings["loc_approx"] == "ubicación aproximada (experimental)"
    html = client.get("/en/productos/usach.flare.nrt/").content.decode()
    strings = json.loads(html.split('id="fp-i18n" type="application/json">', 1)[1].split("</script>", 1)[0])
    assert strings["alert"] == "alert" and strings["loc_approx"] == "approximate location (experimental)"


# --- D-117: geometry and the box -----------------------------------------------------

def test_loc_shape_from_flare_nrts_geometry():
    geometry = flare.parse_geometry(GEOMETRY)
    shape = flare.loc_shape({"bbox_arcsec": [0, 0, 48, 96], "centroid_arcsec": [24, 48]}, geometry)
    assert shape == {"approx": False, "left": "50.00", "top": "46.09", "width": "1.95", "height": "3.91",
                     "cx": "50.98", "cy": "48.05"}
    # The disk centre and scale are flare-nrt's, not assumed: move them.
    moved = flare.parse_geometry(dict(GEOMETRY, disk_center_px=[250.0, 260.0], arcsec_per_px=[2.4, 2.4]))
    shape = flare.loc_shape({"bbox_arcsec": [0, 0, 48, 96]}, moved)
    assert shape["left"] == f"{100 * 250 / 512:.2f}" and shape["top"] == f"{100 * (260 - 40) / 512:.2f}"
    assert shape["width"] == f"{100 * 20 / 512:.2f}"
    # Clipped to the image; off it, nothing.
    clipped = flare.loc_shape({"bbox_arcsec": [1200, -100, 1400, 100]}, geometry)
    assert float(clipped["left"]) + float(clipped["width"]) == pytest.approx(100)
    assert flare.loc_shape({"bbox_arcsec": [2000, 0, 2100, 10]}, geometry) is None
    # A centroid alone: a zero box (drawn as the circle).
    dot = flare.loc_shape({"centroid_arcsec": [0, 0]}, geometry)
    assert dot["cx"] == "50.00" and dot["width"] == "0.00"
    # No geometry, no box; a robust location is approximate.
    assert flare.loc_shape(FIRST_RULE, None) is None
    assert flare.loc_shape(ROBUST, geometry)["approx"] is True
    assert flare.loc_shape(FIRST_RULE, geometry)["approx"] is False


@pytest.mark.parametrize("bad", [None, {}, {"size_px": 512}, dict(GEOMETRY, arcsec_per_px=[0, 4.8]),
                                 dict(GEOMETRY, disk_center_px=[256.0]), dict(GEOMETRY, size_px="512")])
def test_parse_geometry_refuses(bad):
    assert flare.parse_geometry(bad) is None


def test_live_geometry_reference_and_own_scale():
    ref = flare.parse_geometry(GEOMETRY)
    loc = {"area_px": 46, "area_arcsec2": 46 * 4.7 ** 2}
    assert flare.location_geometry(loc, ref)["ax"] == pytest.approx(4.7)
    assert flare.location_geometry({}, ref) == ref
    own = flare.location_geometry(loc, None)
    assert own["ax"] == pytest.approx(4.7) and own["cx"] == 256
    assert flare.location_geometry({"centroid_arcsec": [1, 2]}, None) is None


@pytest.mark.django_db
def test_replay_frames_carry_the_box_from_start_to_end(service):
    start, _peak, end = _replay(service)
    data = flare.replay_payload(3, 0.8)
    inside = [f for f in data["frames"] if start.timestamp() <= f["t"] <= end.timestamp()]
    outside = [f for f in data["frames"] if f not in inside]
    assert len(inside) == 5 and all(f["loc"] and f["loc"]["approx"] is False for f in inside)
    assert outside and all(f["loc"] is None for f in outside)
    # The event's box, not each frame's own location; each frame's geometry.
    assert inside[0]["loc"] == flare.loc_shape(FIRST_RULE, flare.parse_geometry(GEOMETRY))


@pytest.mark.django_db
def test_a_frames_own_geometry_is_used(service):
    _replay(service, frame_geometry=dict(GEOMETRY, disk_center_px=[300.0, 256.0]))
    data = flare.replay_payload(3, 0.8)
    f = next(f for f in data["frames"] if f["loc"])
    assert f["loc"]["left"] == f"{100 * (300 - 744 / 4.8) / 512:.2f}"
    # The panel's box: the peak frame's geometry (here the replay's).
    assert data["location"]["shape"]["left"] == f"{100 * (256 - 744 / 4.8) / 512:.2f}"


# --- D-116: in words -------------------------------------------------------------------

def test_heliographic_words():
    june = dt.datetime(2026, 6, 6, 12, tzinfo=dt.UTC)  # B0 ≈ 0
    b0, radius = flare._sun_b0_and_radius(june)
    assert abs(b0) < 0.2 and 944 < radius < 947
    assert flare.fmt_heliographic({"centroid_arcsec": [radius * math.sin(math.radians(30)), 0]}, june) == "N00 W30"
    assert flare.fmt_heliographic({"centroid_arcsec": [-radius * math.sin(math.radians(68)), 0]}, june) == "N00 E68"
    assert flare.fmt_heliographic({"centroid_arcsec": [0, -radius * math.sin(math.radians(12))]}, june) == "S12 W00"
    # Early September B0 = +7.25°: the disk centre is at N07.
    september = dt.datetime(2026, 9, 7, tzinfo=dt.UTC)
    assert flare._sun_b0_and_radius(september)[0] == pytest.approx(7.25, abs=0.05)
    assert flare.fmt_heliographic({"centroid_arcsec": [0, 0]}, september) == "N07 W00"
    assert flare._sun_b0_and_radius(dt.datetime(2026, 3, 6, tzinfo=dt.UTC))[0] == pytest.approx(-7.25, abs=0.05)
    # Beyond the limb: on the limb.
    assert flare.fmt_heliographic({"centroid_arcsec": [1200, 0]}, june) == "N00 W90"
    # The frame's own R☉ (geometry) when given.
    assert flare.fmt_heliographic({"centroid_arcsec": [500, 0]}, june, rsun_arcsec=1000) == "N00 W30"
    assert flare.fmt_heliographic({"centroid_arcsec": None}, june) == ""
    assert flare.fmt_heliographic({"centroid_arcsec": [1, 1]}, None) == ""


@pytest.mark.django_db
def test_table_and_panel_in_words(client, service, public):
    service.payloads["/api/events"]["events"][0]["location"] = FIRST_RULE
    _replay(service)
    _publish_v2()
    html = _page(client)
    row = _tbody(html).split('data-ev="3"', 1)[1].split("</tr>", 1)[0]
    words = flare.fmt_heliographic(FIRST_RULE, flare.parse_time(service.payloads["/api/events"]["events"][0]["t_pmax"]),
                                   GEOMETRY["rsun_arcsec"])
    assert words and f"<td data-ev-loc-cell>{words}</td>" in row and "″" not in row
    panel = _panel(html)
    assert f"<dd data-ev-loc>{words} <span class=\"flare-exp\">experimental</span></dd>" in panel
    # Event 2 has none: "—".
    row2 = _tbody(html).split('data-ev="2"', 1)[1].split("</tr>", 1)[0]
    assert "<td data-ev-loc-cell>—</td>" in row2


@pytest.mark.django_db
def test_robust_location_dashed_and_approximate(client, service, public):
    events = service.payloads["/api/events"]["events"]
    events[0]["location"] = ROBUST
    _replay(service, location=ROBUST)
    _publish_v2()
    html = _page(client)
    row = _tbody(html).split('data-ev="3"', 1)[1].split("</tr>", 1)[0]
    assert 'title="ubicación aproximada (experimental)">≈ </span>' in row
    panel = _panel(html)
    assert '<span class="flare-exp">aproximada, experimental</span>' in panel
    assert 'class="flare-loc is-approx"' in panel and "ubicación aproximada (experimental)" in panel
    assert flare.replay_payload(3, 0.8)["location"]["approx"] is True


@pytest.mark.django_db
def test_panel_box_and_words_from_the_replay_when_events_has_none(client, service, public):
    service.payloads["/api/events"]["events"][0]["location"] = None
    _replay(service)
    _publish_v2()
    panel = _panel(_page(client))
    assert 'class="flare-loc"' in panel and "data-loc-row>" in panel and "data-loc-row hidden" not in panel
    assert "Mostrar ubicación" in panel and "ubicación estimada (experimental)" in panel
    assert "<dd data-ev-loc>—" not in panel
    # The box's numbers: a decimal point, never a localised comma.
    style = panel.split('class="flare-loc" style="', 1)[1].split('"', 1)[0]
    assert "," not in style and "--cx:" in style


@pytest.mark.django_db
def test_panel_without_geometry_has_words_but_no_box(client, service, public):
    _replay(service, new=False)
    _publish_v2()
    panel = _panel(_page(client))
    assert not re.search(r'class="flare-loc( is-approx)?"', panel) and "data-loc-row hidden" in panel
    assert "<dd data-ev-loc>—" not in panel


# --- live: the tiles and the loop while an event is open -------------------------------

def _open_event(service, location):
    now_slot = service.end
    start = now_slot - dt.timedelta(minutes=6)
    status = dict(service.payloads["/api/status"], flare_detected=True, event_t_start=_iso(start),
                  observation_time=_iso(now_slot), location=location)
    service.payloads["/api/status"] = status
    service.payloads["/api/loop"] = {"channel": "171", "hours": 6, "frames": [
        {"slot": _iso(t), "t_obs": None, "url": f"/api/render/{_iso(t)}/171.webp", "probability": 0.5,
         "state": "active"} for t in (now_slot - dt.timedelta(minutes=2 * k) for k in range(8, -1, -1))]}
    _publish_v2(probability=0.93, alert=True, state="active", flare_detected=True, event_id=7,
                event_t_start=_iso(start), location=location)
    return start


@pytest.mark.django_db
def test_live_tiles_131_and_171_only(client, service, public):
    _replay(service)  # the newest replay: its geometry
    _open_event(service, FIRST_RULE)
    html = _page(client)
    grid = html.split("data-flare-grid", 1)[1].split("</div>\n    <p", 1)[0]
    tiles = grid.split('<figure class="flare-tile">')[1:]
    with_loc = [t.split('data-wave="', 1)[1].split('"', 1)[0] for t in tiles if "flare-loc" in t]
    assert with_loc == ["131", "171", "131", "171"]  # images, then differences
    assert "Mostrar ubicación" in html and "data-lb-loc hidden" in html
    words = flare.fmt_heliographic(FIRST_RULE, service.end - dt.timedelta(minutes=10))
    assert f"ubicación {words}" in html


@pytest.mark.django_db
def test_live_box_needs_a_geometry(client, service, public):
    # No replay with a geometry and no scale in the location: words, no box.
    _open_event(service, {"centroid_arcsec": [-412.3, 268.9], "bbox_arcsec": [-460.8, 230.4, -364.8, 307.2]})
    html = _page(client)
    assert not re.search(r'class="flare-loc( is-approx)?"', html.split("data-flare-events", 1)[0])
    assert "ubicación N" in html or "ubicación S" in html


@pytest.mark.django_db
def test_loop_frames_since_the_event_carry_the_location(client, service, public):
    _replay(service)
    start = _open_event(service, ROBUST)
    data = json.loads(client.get("/productos/usach.flare.nrt/datos/bucle.json").content)
    locs = [(f["t"], f["loc"]) for f in data["frames"]]
    assert all((loc is not None) == (t >= start.timestamp()) for t, loc in locs)
    assert sum(1 for _t, loc in locs if loc) == 4 and all(loc["approx"] for _t, loc in locs if loc)


@pytest.mark.django_db
def test_loop_without_an_open_event_has_no_location(client, service, public):
    _replay(service)
    service.payloads["/api/loop"] = {"channel": "171", "hours": 6, "frames": [
        {"slot": _iso(service.end), "t_obs": None, "url": f"/api/render/{_iso(service.end)}/171.webp",
         "probability": 0.05, "state": "quiet"}]}
    data = json.loads(client.get("/productos/usach.flare.nrt/datos/bucle.json").content)
    assert [f["loc"] for f in data["frames"]] == [None]
