"""v0.6.2 (D-106..D-110), the maintainer's review of v0.6.1: the home
page's four thumbnails in a fixed order and its linked offer card; the flare
page's image grid and lightbox, its order, the "Eventos" section (the alert
table as the selector, ?evento=<id>, the selected event's panel with the
131 Å fallback and the vertical colour bar), the table's markup, and the
player's strings. What the browser does with them (clicks, keyboard, the
lightbox, the player starting at ~20 %) is checked by
.github/scripts/screenshots.py in CI."""

import datetime as dt
import re

import pytest
from django.test import override_settings

from web import flare, timefmt
from web.tests.test_flare_page import PAGE, FakeService, _iso, _publish_v2, _slot_now

MEDIA = "/productos/usach.flare.nrt"


@pytest.fixture
def service(monkeypatch):
    fake = FakeService()
    events = fake.payloads["/api/events"]["events"]
    events[0]["replay"] = True   # event 3, 5 h ago, with a map
    events[1]["replay"] = False  # event 2, 2 days ago, no peak time, no map
    monkeypatch.setattr(flare.httpx, "get", fake.get)
    return fake


@pytest.fixture
def public(settings):
    settings.SWX_FLARE_PAGE_PUBLIC = True


def _page(client, query=""):
    return client.get(PAGE + query).content.decode()


def _section(html, start, end):
    return html.split(start, 1)[1].split(end, 1)[0]


def _tbody(html):
    return _section(html, "<tbody>", "</tbody>")


def _panel(html):
    return _section(html, 'id="flare-ev"', "data-ev-replay")


def _add_event(service, **fields):
    service.payloads["/api/events"]["events"].append(fields)


# --- home ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_home_four_thumbnails_in_a_fixed_order_171_selected(client, service, public):
    valid_from = _slot_now() - dt.timedelta(minutes=10)
    _publish_v2(valid_from=valid_from)
    html = client.get("/").content.decode()
    block = _section(html, "data-sun-now", "</section>")
    thumbs = re.findall(r'<button type="button" class="sun-thumb( on)?" data-sun-thumb data-wave="(\d+)"'
                        r'.*?aria-pressed="(true|false)"', block)
    assert [w for _on, w, _p in thumbs] == ["131", "171", "304", "1600"]
    assert [(on, pressed) for on, w, pressed in thumbs] == [("", "false"), (" on", "true"), ("", "false"),
                                                           ("", "false")]
    main = block.split("data-sun-main")[1].split(">")[0]
    assert 'data-wave="171"' in main and "171.png?kind=img" in main and "size=1024" in main
    # Each thumbnail knows its own large image; nothing in the markup swaps them.
    for wave in ("131", "171", "304", "1600"):
        assert f'data-wave="{wave}" data-large="{MEDIA}/img/{wave}.png?kind=img&amp;slot={_iso(valid_from)}' \
               "&amp;format=webp&amp;size=1024" in block
    assert "data-thumb" not in block and "data-label" not in block  # v0.6.1's swap bookkeeping is gone


@pytest.mark.django_db
def test_home_flare_card_links_to_the_flare_page(client, service, public):
    _publish_v2()  # the product exists once the collector has run
    hero = _section(client.get("/").content.decode(), 'class="offer"', "</section>")
    cards = hero.split("<article")[1:]
    flare_card = next(c for c in cards if "Detección de fulguraciones (NRT)" in c)
    dbdt_card = next(c for c in cards if "dB/dt global" in c)
    data_card = next(c for c in cards if "Datos abiertos y API" in c)
    assert 'class="ocard has-link"' in flare_card and f'<a href="{PAGE}">' in flare_card
    assert 'class="ocard has-link"' in data_card
    assert 'class="ocard"' in dbdt_card and "<a " not in dbdt_card  # in development: no link


@pytest.mark.django_db
def test_home_flare_card_unlinked_without_the_product(client, service, public):
    hero = _section(client.get("/").content.decode(), 'class="offer"', "</section>")
    flare_card = next(c for c in hero.split("<article")[1:] if "Detección de fulguraciones" in c)
    assert "<a " not in flare_card  # no Product row yet: its page would be a 404


# --- flare page: images ---------------------------------------------------------------

@pytest.mark.django_db
def test_image_grid_eight_tiles_images_then_differences(client, service, public):
    valid_from = _slot_now() - dt.timedelta(minutes=10)
    _publish_v2(valid_from=valid_from)
    html = _page(client)
    grid = _section(html, "data-flare-grid", "</section>")
    tiles = re.findall(r'data-tile data-wave="(\d+)" data-kind="(img|diff)" data-full="([^"]+)"', grid)
    assert [(w, k) for w, k, _f in tiles] == [(w, "img") for w in flare.WAVES] + [(w, "diff") for w in flare.WAVES]
    slot = _iso(valid_from)
    for wave, kind, full in tiles:
        # The tile: 512 px WebP for the stored slot; the lightbox: 1024 px
        # for an image, 512 for a difference (the service has no 1024 one).
        assert f"{MEDIA}/img/{wave}.png?kind={kind}&amp;slot={slot}&amp;format=webp\"" in grid
        assert full.endswith("&amp;size=1024") is (kind == "img")
    # Each tile labelled with channel, kind and T_OBS; a button for the dialog.
    assert grid.count("data-tile-title") == 8 and grid.count("data-tile-when") == 8
    assert grid.count('aria-haspopup="dialog" aria-controls="flare-lightbox"') == 8
    assert "131 Å · imagen" in grid and "1600 Å · diferencia" in grid
    assert '<time datetime="' in _section(grid, 'data-wave="304" data-kind="diff"', "</figure>")
    # Only the differences are lazy (below the fold on a phone).
    assert grid.count('loading="lazy"') == 4


@pytest.mark.django_db
def test_lightbox_is_a_dialog_with_a_close_button(client, service, public):
    _publish_v2()
    box = _section(_page(client), '<dialog class="flare-lightbox"', "</dialog>")
    assert 'id="flare-lightbox" aria-labelledby="flare-lb-title"' in box
    assert "data-lb-close" in box and ">Cerrar<" in box and "data-lb-img" in box
    # No image is fetched for it until a tile is opened.
    assert 'data-lb-img alt="" decoding="async">' in box


@pytest.mark.django_db
def test_loop_player_under_the_grid_unchanged(client, service, public):
    _publish_v2()
    html = _page(client)
    images = _section(html, 'id="flare-img-h"', 'id="flare-time-h"')
    assert images.index("data-flare-grid") < images.index('id="flare-loop"')
    assert 'data-player data-target="flare-loop-frame"' in images and 'id="flare-loop-frame" hidden' in images
    assert 'data-kind="img"' in images and 'data-kind="diff"' in images and "Ver las últimas 6 h (171 Å)" in images
    assert "/render/" not in html  # nothing loaded until pressed


# --- flare page: order and events -------------------------------------------------------

@pytest.mark.django_db
def test_layout_order(client, service, public):
    _publish_v2()
    html = _page(client)
    markers = ['id="flare-img-h"', "data-flare-grid", 'id="flare-loop"', 'id="flare-time-h"',
               'class="card swx-stack', 'id="flare-ev-h"', "<tbody>", 'id="flare-ev"', "Qué muestra",
               "Ficha del producto"]
    positions = [html.index(m) for m in markers]
    assert positions == sorted(positions)
    assert "Qué miró el modelo" not in html and 'id="flare-cam-h"' not in html  # now inside "Eventos"
    assert 'id="flare-replay"' not in html  # the replay plays in the panel, not a separate section


@pytest.mark.django_db
def test_table_markup_no_parentheses_short_headers_two_line_times(client, service, public):
    end = service.end
    start = (end - dt.timedelta(days=3)).replace(hour=23, minute=52)
    # An event across midnight: its peak and end carry their own date.
    _add_event(service, event_id=9, t_start=_iso(start), t_pmax=_iso(start + dt.timedelta(minutes=10)),
               t_end=_iso(start + dt.timedelta(minutes=14)), p_max=0.95, n_alert_frames=3,
               location={"centroid_arcsec": [120.0, -30.4]}, closed=True,
               gradcam=f"/api/gradcam/{_iso(start + dt.timedelta(minutes=10))}.png", replay=False)
    _publish_v2()
    html = _page(client)
    table = _section(html, '<table class="status-table perf-table flare-hist">', "</table>")
    head = _section(table, "<thead>", "</thead>")
    body = _tbody(html)
    cells = re.sub(r"<[^>]+>", "", body)
    assert "(" not in cells and ")" not in cells
    assert ">P máx.</abbr>" in head and ">Imág.</abbr>" in head
    for gone in ("Grad-CAM", "Repetición", "Prob. máx.", "Imágenes en alerta</th>"):
        assert gone not in head
    assert "Grad-CAM" not in body and "ver repetición" not in body
    rows = {re.search(r'data-ev="(\d+)"', r).group(1): r for r in body.split("<tr")[1:]}
    assert list(rows) == ["3", "2", "9"]  # newest first
    ev3, ev9 = rows["3"], rows["9"]
    t3 = end - dt.timedelta(hours=5)
    assert f'<time datetime="{_iso(t3)}" data-ut="dt" data-stack>{t3:%d/%m %H:%M} UT</time>' in ev3
    p3 = t3 + dt.timedelta(minutes=4)
    if p3.date() == t3.date():
        assert f'data-ut="t" data-stack>{p3:%H:%M} UT</time>' in ev3  # same day: time only
    assert f'data-ut="dt" data-stack>{start:%d/%m %H:%M} UT</time>' in ev9
    peak9 = start + dt.timedelta(minutes=10)
    assert f'data-ut="dt" data-stack>{peak9:%d/%m %H:%M} UT</time>' in ev9  # next day: with its date
    assert "120″, −30″" in ev9


@pytest.mark.django_db
def test_rows_are_the_selector(client, service, public):
    _publish_v2()
    body = _tbody(_page(client))
    rows = body.split("<tr")[1:]
    assert 'data-ev="3" tabindex="0" class="is-sel" aria-current="true"' in rows[0]
    assert 'data-ev="2" tabindex="0">' in rows[1]
    assert '<a class="sbtn" href="?evento=3#flare-ev" tabindex="-1" data-ev-link>ver</a>' in rows[0]
    assert '<a class="sbtn" href="?evento=2#flare-ev"' in rows[1]


@pytest.mark.django_db
def test_rows_without_an_event_id_are_not_selectable(client, service, public):
    _add_event(service, event_id="x", t_start=_iso(service.end - dt.timedelta(days=5)), closed=True)
    _publish_v2()
    rows = _tbody(_page(client)).split("<tr")[1:]
    assert len(rows) == 3 and "data-ev=" not in rows[2] and "tabindex" not in rows[2] and "?evento=" not in rows[2]


@pytest.mark.django_db
def test_default_selection_most_recent_then_the_open_event(client, service, public):
    _publish_v2()
    assert 'data-ev-id="3"' in _panel(_page(client))  # no open event: the most recent
    end = service.end
    _add_event(service, event_id=4, t_start=_iso(end - dt.timedelta(minutes=6)), t_pmax=_iso(end - dt.timedelta(minutes=2)),
               t_end=_iso(end), p_max=0.93, closed=False, gradcam=None, replay=False)
    # A newer closed event doesn't win over the open one.
    flare.cache.clear()
    panel = _panel(_page(client))
    assert 'data-ev-id="4"' in panel and "en curso" in panel


@pytest.mark.django_db
@pytest.mark.parametrize("query, selected", [("?evento=2", "2"), ("?evento=3", "3"), ("?evento=99", "3"),
                                             ("?evento=abc", "3"), ("?evento=-2", "3"), ("?evento=0", "3"),
                                             ("?evento=1234567890", "3")])
def test_selection_from_the_url(client, service, public, query, selected):
    _publish_v2()
    html = _page(client, query)
    assert f'data-ev-id="{selected}"' in _panel(html)
    sel = [r for r in _tbody(html).split("<tr")[1:] if 'class="is-sel"' in r]
    assert len(sel) == 1 and f'data-ev="{selected}"' in sel[0]


def test_parse_event_param():
    assert flare.parse_event_param("42") == 42
    for bad in (None, "", "0", "-1", "4a", "1e3", " 4", "1234567890", "４", "٤", "²"):
        assert flare.parse_event_param(bad) is None, bad


@pytest.mark.django_db
def test_every_event_has_its_panel_template(client, service, public):
    _publish_v2()
    html = _page(client)
    for event_id in ("3", "2"):
        tpl = _section(html, f'<template id="flare-ev-tpl-{event_id}">', "</template>")
        assert f'data-ev-id="{event_id}"' in tpl and "Evento del " in tpl


@pytest.mark.django_db
def test_panel_heat_over_131_with_vertical_bar_and_facts(client, service, public):
    _publish_v2()
    panel = _panel(_page(client))
    peak = service.end - dt.timedelta(hours=5) + dt.timedelta(minutes=4)
    assert f'class="flare-cam-heat" src="{MEDIA}/gradcam/{_iso(peak)}.png?style=heat&amp;format=webp"' in panel
    # Recent: the live 131 Å image first, the replay's stored copy next.
    assert f'class="flare-cam-base" src="{MEDIA}/img/131.png?kind=img&amp;slot={_iso(peak)}&amp;format=webp"' \
           f' data-fallback="{MEDIA}/replay/3/{_iso(peak)}/131.webp"' in panel
    bar = _section(panel, 'class="cam-vbar"', "</div>")
    assert bar.index("máx.") < bar.index(">0<")  # "máx." at the top, 0 at the bottom
    assert "cam-bar\"" not in panel and "máximo del mapa" not in panel  # the wide horizontal bar is gone
    assert "data-cam-opacity" in panel and 'style="opacity:.6"' in panel
    for text in ("Probabilidad máx.", "0,91", "Inicio", "Máximo", "Fin", "Ubicación", "−412″, 269″", "Clase GOES"):
        assert text in panel, text
    # The replay: a button in the panel, playing in the panel's own frame.
    assert f'data-replay="{MEDIA}/datos/repeticion-3.json"' in panel
    assert f'data-replay-xray="{MEDIA}/datos/rayosx-3.json"' in panel
    html = _page(client)
    assert 'data-player data-target="flare-ev-frame"' in html and 'id="flare-ev-frame"' in panel


@pytest.mark.django_db
def test_old_event_131_from_the_replay_else_the_map_alone(client, service, public):
    end = service.end
    old = end - dt.timedelta(days=6)
    for event_id, replay in ((7, True), (8, False)):
        start = old - dt.timedelta(hours=event_id)
        peak = start + dt.timedelta(minutes=4)
        _add_event(service, event_id=event_id, t_start=_iso(start), t_pmax=_iso(peak),
                   t_end=_iso(start + dt.timedelta(minutes=8)), p_max=0.9, closed=True,
                   gradcam=f"/api/gradcam/{_iso(peak)}.png", replay=replay)
    _publish_v2()
    p7 = _iso(old - dt.timedelta(hours=7) + dt.timedelta(minutes=4))
    panel = _panel(_page(client, "?evento=7"))
    # Past flare-nrt's 4 days of FITS: straight to the replay's own 131 Å.
    assert f'class="flare-cam-base" src="{MEDIA}/replay/7/{p7}/131.webp"' in panel and "data-fallback" not in panel
    assert "data-cam-nobase hidden" in panel
    panel = _panel(_page(client, "?evento=8"))
    assert "flare-cam-base" not in panel and "flare-cam-heat" in panel
    assert "data-cam-nobase>" in panel and "se muestra solo el mapa" in panel


@pytest.mark.django_db
def test_panel_without_a_map(client, service, public):
    end = service.end
    _add_event(service, event_id=4, t_start=_iso(end - dt.timedelta(minutes=6)), t_pmax=None, t_end=_iso(end),
               p_max=0.93, closed=False, gradcam=None, replay=False)
    _add_event(service, event_id=5, t_start=_iso(end - dt.timedelta(days=3)), t_pmax=_iso(end - dt.timedelta(days=3)),
               p_max=0.42, closed=True, gradcam=f"/api/gradcam/{_iso(end - dt.timedelta(days=3))}.png")
    _publish_v2()
    panel = _panel(_page(client))
    assert 'data-ev-id="4"' in panel and "todavía no está disponible" in panel and "gradcam/" not in panel
    panel = _panel(_page(client, "?evento=5"))  # below the threshold: never a map
    assert "Sin mapa Grad-CAM para este evento." in panel and "gradcam/" not in panel


@pytest.mark.django_db
def test_no_events_keeps_the_empty_text(client, service, public):
    service.payloads["/api/events"] = {"events": []}
    _publish_v2()
    html = _page(client)
    events = _section(html, 'id="flare-ev-h"', "</section>")
    assert "Sin alertas en los últimos 30 días." in events
    assert "<table" not in events and 'id="flare-ev"' not in html and "<template" not in html


@pytest.mark.django_db
def test_service_down(client, service, public):
    service.down = True
    _publish_v2()
    response = client.get(PAGE + "?evento=3")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Sin datos del detector." in _section(html, 'id="flare-ev-h"', "</section>")
    assert "data-flare-grid" not in html and 'id="flare-ev"' not in html


@pytest.mark.django_db
def test_page_en(client, service, public):
    _publish_v2()
    html = client.get("/en" + PAGE).content.decode()
    for text in ("Events", "Choose a row to see the event.", ">P max.</abbr>", ">Img.</abbr>", "Event of ",
                 "Max. probability", "131 Å · image", "1600 Å · difference", "Tap an image to see it full size.",
                 "The last 6 h (171 Å)", "max.", "Close"):
        assert text in html, text


# --- pieces ---------------------------------------------------------------------------------

def test_stacked_time_element():
    when = dt.datetime(2026, 11, 12, 21, 34, tzinfo=dt.UTC)
    assert timefmt.time_html(when, "es", stacked=True) == \
        '<time datetime="2026-11-12T21:34:00Z" data-ut="dt" data-stack>12/11 21:34 UT</time>'
    assert timefmt.time_html(when, "es", date=False) == '<time datetime="2026-11-12T21:34:00Z" data-ut="t">21:34 UT</time>'


@pytest.mark.django_db
def test_player_strings(client, service, public):
    _publish_v2()
    html = _page(client)
    assert '"loading": "cargando %(n)s/%(total)s"' in html and '"pending_frame": "cargando\\u2026"' in html


def test_dev_media_serves_replay_131(rf, monkeypatch):
    """The DEBUG stand-in for nginx (runserver, CI) forwards a replay's 131 Å
    frame like nginx does (D-107) and nothing else."""
    import httpx

    from web import views

    seen = []

    def get(url, params=None, timeout=None, **kw):
        seen.append(url)
        return httpx.Response(200, content=b"RIFF....WEBP", headers={"content-type": "image/webp"})

    monkeypatch.setattr(httpx, "get", get)
    slot = "2026-11-11T12:02:00Z"
    with override_settings(DEBUG=True):
        response = views.flare_media_dev(rf.get("/"), f"replay/3/{slot}/131.webp")
        assert response.status_code == 200 and seen[-1].endswith(f"/api/events/3/frames/{slot}/131.webp")
        from django.http import Http404
        with pytest.raises(Http404):
            views.flare_media_dev(rf.get("/"), f"replay/3/{slot}/304.webp")
