"""v0.6.2, second part (D-112): an event's Grad-CAM and 131 Å from its peak
set (flare-nrt 1.2.0, `GET /api/events/{id}/peak/{name}.webp`), the order
of the fallbacks, only the names replay.json's `peak.images` lists, and the
DEBUG stand-in for the new nginx location. Event 1 is the real case of
2026-11-11 21:34 UT: its Grad-CAM was made by 1.0.0, so
gradcam/<slot>.png?style=heat is a 404 and only the peak set has the heat
map. What the browser does with the chains (moving on at an error, the
overlay label, never a broken image) is checked by
.github/scripts/screenshots.py against the stub."""

import datetime as dt
import json

import pytest
from django.test import override_settings

from web import flare
from web.tests.test_flare_page import FakeService, _iso, _publish_v2
from web.tests.test_flare_v062 import MEDIA, _chain, _page, _panel

ALL = ["131", "171", "304", "1600", "gradcam-heat", "gradcam-overlay"]


def _event1(fake, *, peak_images=ALL, frame_heat=False, peak_block=True, age=dt.timedelta(days=1, hours=16)):
    start = fake.end - age
    peak = start + dt.timedelta(minutes=4)
    fake.payloads["/api/events"]["events"].append({
        "event_id": 1, "t_start": _iso(start), "t_pmax": _iso(peak), "t_end": _iso(peak + dt.timedelta(minutes=4)),
        "p_max": 0.9, "n_alert_frames": 3, "location": None, "closed": True,
        "gradcam": f"/api/gradcam/{_iso(peak)}.png", "replay": True})
    replay = {
        "schema": "flare-nrt-replay/1",
        "event": {"event_id": 1, "t_start": _iso(start), "t_peak": _iso(peak), "t_end": _iso(peak)},
        "window": {"start": _iso(start - dt.timedelta(minutes=30)), "end": _iso(peak + dt.timedelta(minutes=60))},
        "n_slots": 2,
        "frames": [
            {"slot": _iso(start), "probability": 0.85, "images": ["171", "131"]},
            {"slot": _iso(peak), "probability": 0.9,
             "images": ["171", "131", "gradcam-heat"] if frame_heat else ["171", "131"]},
        ],
        "complete": True, "missing": {"171": [], "gradcam-heat": [], "131": [], "peak": []},
    }
    if peak_block:
        replay["peak"] = {"slot": _iso(peak), "probability": 0.9, "gradcam_peak": 0.97, "images": peak_images}
    fake.payloads["/api/events/1/replay"] = replay
    return _iso(peak)


@pytest.fixture
def service(monkeypatch):
    fake = FakeService()
    events = fake.payloads["/api/events"]["events"]
    events[0]["replay"] = True   # event 3, 5 h ago, no replay.json here (a 404)
    events[1]["replay"] = False
    monkeypatch.setattr(flare.httpx, "get", fake.get)
    return fake


@pytest.fixture
def public(settings):
    settings.SWX_FLARE_PAGE_PUBLIC = True


@pytest.mark.django_db
def test_event_1_heat_map_from_its_peak_set(client, service, public):
    slot = _event1(service)
    _publish_v2()
    panel = _panel(_page(client, "?evento=1"))
    assert f'class="flare-cam-heat" src="{MEDIA}/replay/1/peak/gradcam-heat.webp"' in panel
    # The order: the peak set, (no heat frame in this replay), the live map,
    # then the overlay (the peak set's, then the live one), labelled as such.
    assert _chain(panel, "flare-cam-heat") == [
        (f"{MEDIA}/replay/1/peak/gradcam-heat.webp", False),
        (f"{MEDIA}/gradcam/{slot}.png?style=heat&format=webp", False),
        (f"{MEDIA}/replay/1/peak/gradcam-overlay.webp", True),
        (f"{MEDIA}/gradcam/{slot}.png?style=overlay&format=webp", True),
    ]
    # 131 Å: the peak set's (the same slot as the map), the live image (2
    # days: flare-nrt still has the FITS), the replay's frame.
    assert _chain(panel, "flare-cam-base") == [
        f"{MEDIA}/replay/1/peak/131.webp",
        f"{MEDIA}/img/131.png?kind=img&slot={slot}&format=webp",
        f"{MEDIA}/replay/1/{slot}/131.webp",
    ]
    assert f'src="{MEDIA}/replay/1/peak/131.webp"' in panel
    # Built from replay.json: flare.js need not read it again.
    assert "data-cam-src" not in panel.split("data-ev-replay")[0].split("<template")[0]
    assert "data-cam-label-overlay hidden" in panel and "data-cam-overlay-note hidden" in panel
    assert "Grad-CAM superpuesto" in panel


@pytest.mark.django_db
def test_only_one_replay_read_for_the_page(client, service, public):
    _event1(service)
    _publish_v2()
    service.calls.clear()
    _page(client, "?evento=1")
    replays = [url for url, _p, _t in service.calls if url.endswith("/replay")]
    assert replays == [service.calls[0][0].split("/api/")[0] + "/api/events/1/replay"]


@pytest.mark.django_db
def test_other_events_are_deferred_until_flare_js_reads_their_replay(client, service, public):
    _event1(service)
    _publish_v2()
    html = _page(client, "?evento=1")
    tpl = html.split('<template id="flare-ev-tpl-3">', 1)[1].split("</template>", 1)[0]
    # No src in a <template> copy: nothing is fetched (or broken) before
    # flare.js has the event's peak set; it reads datos/repeticion-3.json.
    assert 'class="flare-cam-heat" data-chain=' in tpl and 'class="flare-cam-base" data-chain=' in tpl
    assert f'data-cam-src="{MEDIA}/datos/repeticion-3.json"' in tpl
    # An event without a replay has nothing to read: its chain is final.
    service.payloads["/api/events"]["events"][0]["replay"] = False
    flare.cache.clear()
    tpl = _page(client, "?evento=1").split('<template id="flare-ev-tpl-3">', 1)[1].split("</template>", 1)[0]
    assert "data-cam-src" not in tpl and 'class="flare-cam-heat" data-chain=' in tpl


@pytest.mark.django_db
def test_only_names_in_peak_images(client, service, public):
    slot = _event1(service, peak_images=["gradcam-overlay", "bogus", "../../status", "304"])
    _publish_v2()
    panel = _panel(_page(client, "?evento=1"))
    assert _chain(panel, "flare-cam-heat") == [
        (f"{MEDIA}/gradcam/{slot}.png?style=heat&format=webp", False),
        (f"{MEDIA}/replay/1/peak/gradcam-overlay.webp", True),
        (f"{MEDIA}/gradcam/{slot}.png?style=overlay&format=webp", True),
    ]
    assert f"{MEDIA}/replay/1/peak/131.webp" not in panel and "bogus" not in panel and "status" not in panel


@pytest.mark.django_db
def test_replay_without_a_peak_set_uses_its_heat_frame(client, service, public):
    """A replay built by 1.1.0 (no `peak`): the replay's heat frame at the
    peak slot comes before the live map; past 4 days, no live image."""
    slot = _event1(service, peak_block=False, frame_heat=True, age=dt.timedelta(days=6))
    _publish_v2()
    panel = _panel(_page(client, "?evento=1"))
    assert _chain(panel, "flare-cam-heat") == [
        (f"{MEDIA}/replay/1/{slot}/gradcam-heat.webp", False),
        (f"{MEDIA}/gradcam/{slot}.png?style=heat&format=webp", False),
        (f"{MEDIA}/gradcam/{slot}.png?style=overlay&format=webp", True),
    ]
    assert _chain(panel, "flare-cam-base") == [f"{MEDIA}/replay/1/{slot}/131.webp"]


@pytest.mark.django_db
def test_replay_data_carries_the_peak_set_and_the_sources(client, service, public):
    slot = _event1(service, peak_images=["131", "gradcam-heat", "nope"])
    _publish_v2()
    data = json.loads(client.get(f"{MEDIA}/datos/repeticion-1.json").content)
    assert data["peak"] == {"slot": slot, "t": int(flare.parse_time(slot).timestamp()), "probability": 0.9,
                            "gradcam_peak": 0.97,
                            "images": {"131": f"{MEDIA}/replay/1/peak/131.webp",
                                       "gradcam-heat": f"{MEDIA}/replay/1/peak/gradcam-heat.webp"}}
    assert data["cam"]["slot"] == slot
    assert data["cam"]["heat"][0] == {"src": f"{MEDIA}/replay/1/peak/gradcam-heat.webp", "overlay": False}
    assert data["cam"]["base"][0] == f"{MEDIA}/replay/1/peak/131.webp"
    # The service down: no peak, no sources; the panel keeps its own chain.
    service.down = True
    flare.cache.clear()
    data = json.loads(client.get(f"{MEDIA}/datos/repeticion-1.json").content)
    assert data["unavailable"] and data["peak"] is None and data["cam"] is None


def test_peak_url_refuses_other_names():
    assert flare.peak_url(1, "gradcam-heat") == f"{MEDIA}/replay/1/peak/gradcam-heat.webp"
    with pytest.raises(ValueError):
        flare.peak_url(1, "../status")


def test_dev_media_serves_the_peak_set(rf, monkeypatch):
    import httpx
    from django.http import Http404

    from web import views

    seen = []

    def get(url, params=None, timeout=None, **kw):
        seen.append(url)
        return httpx.Response(200, content=b"RIFF....WEBP", headers={"content-type": "image/webp"})

    monkeypatch.setattr(httpx, "get", get)
    with override_settings(DEBUG=True):
        for name in ALL:
            response = views.flare_media_dev(rf.get("/"), f"replay/1/peak/{name}.webp")
            assert response.status_code == 200 and seen[-1].endswith(f"/api/events/1/peak/{name}.webp")
        for bad in ("replay/1/peak/94.webp", "replay/x/peak/131.webp", "replay/1/peak/131.png"):
            with pytest.raises(Http404):
                views.flare_media_dev(rf.get("/"), bad)
