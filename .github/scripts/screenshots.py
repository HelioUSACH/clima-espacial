"""Release screenshots + browser smoke checks (18-HANDOFF-v0.2.0.md §3;
supersedes the uPlot-era version, D-058).

    python .github/scripts/screenshots.py http://127.0.0.1:8000 shots/ [--quiet-flare]

Against a running portal with seeded data (`manage.py seed_dev_data`), for
home, a few product pages and the forecast baseline's page x 1440/390 px x
day/night x ES/EN:

- waits for exactly one ECharts canvas group per stack and saves a
  full-page screenshot (to sit next to the matching mockup artboard in the
  PR, per 18-HANDOFF "Done means");
- fails if the stack count is wrong, if any product's status overlay is
  visible, or if the page logs a JS error;
- once per page (ES, day, desktop): checks that "reset zoom" restores the
  full range after a programmatic zoom, and that the range-click race fix
  (16-PERF B1) and client cache (B2) both still hold with the new engine.

Exit status 1 on any failure, with every failure listed.
"""

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = {
    # name: (path, expected chart count -- one ECharts instance per stack,
    # not one per panel: home's 5-panel solar-wind stack is ONE instance --
    # and which interaction checks run on it, once, in ES/day/desktop).
    "home": ("/", 1, "stack"),
    "noaa.rtsw.mag": ("/productos/noaa.rtsw.mag/", 1, "stack"),
    # v0.4.0 §7: the forecast page (seed_dev_data issues a week of it).
    "usach.kp.persistence": ("/productos/usach.kp.persistence/", 1, "forecast"),
    # v0.4.2: Dst from Kyoto (D-081), and AE, a delayed product whose range
    # ends at its latest datum, not now (D-082).
    "kyoto.dst": ("/productos/kyoto.dst/", 1, None),
    "kyoto.ae": ("/productos/kyoto.ae/", 1, "delayed"),
    # v0.5.2: a daily product (D-097): its own ranges, caption and day label,
    # and markers on its few points.
    "noaa.goes.electron_fluence": ("/productos/noaa.goes.electron_fluence/", 1, "daily"),
    # v0.6.0: the flare detector's page (D-101), against the flare-nrt stub
    # (.github/scripts/flare_nrt_stub.py) with SWX_FLARE_PAGE_PUBLIC=1.
    "usach.flare.nrt": ("/productos/usach.flare.nrt/", 1, "flare"),
    # v0.5.0: the data-access page (no chart): its code examples must show.
    "datos-api": ("/datos-api/", 0, "code"),
}
WIDTHS = {"desktop": 1440, "mobile": 390}
THEMES = ("day", "night")
LANGS = {"es": "", "en": "/en"}
# An explicit browser locale: ECharts/the page build Intl formatters at
# load time, and a runner whose shell locale is e.g. "en_US@posix" hands
# Chromium an invalid language tag that can make those throw before any
# chart exists.
BROWSER_LOCALES = {"es": "es-CL", "en": "en-US"}

COUNT_READY_STACKS = "document.querySelectorAll('[data-stack-plot][data-echarts-ready=\"true\"]').length"
VISIBLE_STATUS = """
[...document.querySelectorAll('.swx-stack-status')]
  .filter(el => el.textContent.trim() && el.offsetParent !== null)
  .map(el => el.textContent.trim())
"""
SPAN_SECONDS = "document.querySelector('.swx-stack').__swxStack.spanSeconds"


def check_delayed(page, failures, label):
    """D-082: a delayed product draws its data (not an empty "now" window)
    and says it is delayed, for every range."""
    for range_key in ("1d", "7d"):
        page.click(f'[data-chart-range="{range_key}"]')
        page.wait_for_function(f"document.querySelector('.swx-stack').dataset.range === '{range_key}'")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)
        meta = page.evaluate("document.querySelector('[data-stack-meta]').textContent")
        if "(con retraso)" not in meta:
            failures.append(f"{label} {range_key}: meta doesn't say delayed: {meta!r}")
        span = page.evaluate(SPAN_SECONDS)
        if not span or span < 0.5 * 86400:
            failures.append(f"{label} {range_key}: delayed chart spans {span} s, expected its data")


DAILY_TEXT = {
    "es": ("valores diarios (días UTC completos)", "Último dato día "),
    "en": ("daily values (complete UTC days)", "Last data point day "),
}


def check_daily(page, failures, label, lang):
    """D-097: the product's ranges, its caption instead of "promedios de N
    min", its latest datum as a day, and a marker on each of its points."""
    keys = page.evaluate("[...document.querySelectorAll('[data-chart-range]')].map(b => b.dataset.chartRange)")
    if keys != ["30d", "90d", "1y", "all"]:
        failures.append(f"{label}: range buttons {keys}, expected 30d/90d/1y/all")
    meta = page.evaluate("document.querySelector('[data-stack-meta]').textContent")
    caption, latest = DAILY_TEXT[lang]
    if caption not in meta or latest not in meta or re.search(r"\d\d:\d\d", meta):
        failures.append(f"{label}: meta {meta!r} lacks {caption!r} / {latest!r} (or names a time)")
    shown = page.evaluate("document.querySelector('.swx-stack').__swxStack.ec.getOption().series"
                          ".filter(s => s.id === 'fluence-0').map(s => s.showSymbol)")
    if shown != [True]:
        failures.append(f"{label}: fluence line markers {shown}, expected [True]")


FLARE_STATE = {"es": "Flare en curso", "en": "Flare in progress"}
QUIET_STATE = {"es": "Calma", "en": "Quiet"}
LOCAL_LABEL = {"es": "hora local", "en": "local time"}
PLAYER = "document.getElementById('{id}').__swxPlayer"


def check_local_time(page, failures, label, lang):
    """D-105: every <time> gets the visitor's local time next to its UT text
    (this browser runs in America/Santiago), computed from `datetime`; the
    switch is shown; with "hora local" first the order flips."""
    import datetime as dt
    from zoneinfo import ZoneInfo

    times = page.evaluate("""[...document.querySelectorAll('time[data-ut]')].map(t => ({
        iso: t.getAttribute('datetime'), kind: t.dataset.ut, text: t.textContent}))""")
    if not times:
        failures.append(f"{label}: no <time> element")
        return
    for t in times[:5]:
        when = dt.datetime.fromisoformat(t["iso"].replace("Z", "+00:00")).astimezone(ZoneInfo("America/Santiago"))
        clock = f"{when:%H:%M:%S}" if t["kind"].endswith("s") else f"{when:%H:%M}"
        if f"{clock} {LOCAL_LABEL[lang]}" not in t["text"] or " UT" not in t["text"]:
            failures.append(f"{label}: <time {t['iso']}> reads {t['text']!r}, expected local {clock}")
    if not page.evaluate("!document.querySelector('[data-tz-switch]').hidden"):
        failures.append(f"{label}: the UT / hora local switch is hidden")


def check_local_switch(page, failures, label):
    page.locator('[data-tz="local"]:visible').first.click()
    first = page.evaluate("document.querySelector('time[data-ut]').textContent")
    stored = page.evaluate("localStorage.getItem('swx-time')")
    if not first.split("(")[0].strip().endswith("hora local") or stored != "local":
        failures.append(f"{label}: after 'hora local' the first time reads {first!r} (stored {stored!r})")
    page.locator('[data-tz="ut"]:visible').first.click()


def wait_images(page, selector, timeout=15000):
    page.wait_for_function(f"[...document.querySelectorAll({selector!r})].every(i => i.complete)", timeout=timeout)
    return page.evaluate(f"[...document.querySelectorAll({selector!r})].map(i => i.naturalWidth > 0)")


def check_flare(page, failures, label, lang, interactive, quiet=False):
    """D-101/D-102/D-106/D-107: the timeline (threshold line, alert spans,
    X-ray panel), the eight image tiles, the selected event's panel (heat
    map over 131 Å, or the map alone where neither 131 Å image exists), the
    state badge and the box, and (once) the lightbox."""
    stack = "document.querySelector('.swx-stack').__swxStack"
    marks = page.evaluate(f"""(() => {{
        const s = {stack}.ec.getOption().series;
        const prob = s.find(x => x.id === 'probability-0');
        return {{lines: prob && prob.markLine ? prob.markLine.data.length : 0,
                 spans: prob && prob.markArea ? prob.markArea.data.length : 0,
                 xray: s.some(x => x.id === 'xray-0')}};
    }})()""")
    if not marks["lines"] or not marks["spans"] or not marks["xray"]:
        failures.append(f"{label}: timeline incomplete {marks} (threshold line, alert spans, X-ray panel)")
    tiles = page.evaluate("[...document.querySelectorAll('[data-tile]')].map(t => t.dataset.wave + ':' + t.dataset.kind)")
    expected_tiles = [f"{w}:{k}" for k in ("img", "diff") for w in ("131", "171", "304", "1600")]
    if tiles != expected_tiles:
        failures.append(f"{label}: image tiles {tiles}, expected {expected_tiles}")
    page.locator("[data-flare-grid] [data-wave='1600'][data-kind='diff']").scroll_into_view_if_needed()
    loaded = wait_images(page, "[data-tile] img, #flare-ev .flare-cam-heat")
    if not loaded or not all(loaded):
        failures.append(f"{label}: images loaded {loaded} (8 tiles, the event's Grad-CAM)")
    badge = page.evaluate("(document.querySelector('.flare-state') || {}).textContent")
    expected = (QUIET_STATE if quiet else FLARE_STATE)[lang]
    if badge != expected:
        failures.append(f"{label}: state badge {badge!r}, expected {expected!r}")
    if page.evaluate("!!document.querySelector('.flare-box')") is quiet:
        failures.append(f"{label}: detection box {'shown' if quiet else 'missing'}")
    if not page.evaluate("!!document.querySelector('#flare-ev .cam-vbar')"):
        failures.append(f"{label}: no event panel with its colour bar (the stub has alerts in the last 30 days)")
    if interactive:
        check_lightbox(page, failures, label)


def check_lightbox(page, failures, label):
    """D-106: a tile opens the dialog with its large image; Esc closes it and
    focus returns to the tile; the close button closes it too."""
    tile = "[data-tile][data-wave='304'][data-kind='diff']"
    page.focus(tile)
    page.keyboard.press("Enter")
    page.wait_for_function("document.getElementById('flare-lightbox').open", timeout=5000)
    page.wait_for_function("(() => { const i = document.querySelector('[data-lb-img]');"
                           " return i.src.includes('304.png?kind=diff') && i.complete && i.naturalWidth > 0; })()",
                           timeout=15000)
    if not page.evaluate("document.activeElement.matches('[data-lb-close]')"):
        failures.append(f"{label}: focus is not on the lightbox's close button")
    page.keyboard.press("Escape")
    page.wait_for_function("!document.getElementById('flare-lightbox').open", timeout=5000)
    if not page.evaluate(f"document.activeElement.matches({tile!r})"):
        failures.append(f"{label}: focus did not return to the tile after Esc")
    page.click("[data-tile][data-wave='171'][data-kind='img']")
    page.wait_for_function("document.getElementById('flare-lightbox').open", timeout=5000)
    if "size=1024" not in page.evaluate("document.querySelector('[data-lb-img]').src"):
        failures.append(f"{label}: the 171 Å image did not open at 1024 px")
    page.click("[data-lb-close]")
    if page.evaluate("document.getElementById('flare-lightbox').open"):
        failures.append(f"{label}: the close button did not close the lightbox")


def selected_event(page):
    return page.evaluate("""({row: (document.querySelector('tr[data-ev][aria-current="true"]') || {dataset: {}}).dataset.ev,
        panel: (document.querySelector('#flare-ev [data-ev-id]') || {dataset: {}}).dataset.evId,
        url: new URL(location.href).searchParams.get('evento')})""")


def check_events(page, failures, label, base_url, out, quiet):
    """D-107: the table selects the event (click on a row, Enter on a focused
    row), the selection is in the URL and a ?evento= link opens on it. The
    stub's event 2 is 3 days old: its live 131 Å image is gone (the stub
    keeps 3 days), so the panel falls back to the replay's own 131 Å frame
    (active) or, with no replay (quiet), shows the map alone."""
    ids = page.evaluate("[...document.querySelectorAll('tr[data-ev]')].map(r => r.dataset.ev)")
    first = selected_event(page)
    want = "3" if quiet else "4"  # the most recent; the open event while one is open
    if not ids or first["row"] != want or first["panel"] != want:
        failures.append(f"{label}: default selection {first}, rows {ids}, expected event {want}")
    page.click('tr[data-ev="2"] td:nth-child(2)')
    sel = selected_event(page)
    if sel != {"row": "2", "panel": "2", "url": "2"}:
        failures.append(f"{label}: a click on event 2's row gave {sel}")
    page.wait_for_function("(() => { const b = document.querySelector('#flare-ev .flare-cam-base');"
                           " const n = document.querySelector('#flare-ev [data-cam-nobase]');"
                           " return (b && !b.hidden && b.complete && b.naturalWidth > 0) || (n && !n.hidden); })()",
                           timeout=15000)
    base = page.evaluate("(() => { const b = document.querySelector('#flare-ev .flare-cam-base');"
                         " return b && !b.hidden ? b.src : null; })()")
    if quiet and base is not None:
        failures.append(f"{label}: event 2 (no replay) shows a 131 Å image {base!r}, expected the map alone")
    if not quiet and (not base or "/replay/2/" not in base or not base.endswith("/131.webp")):
        failures.append(f"{label}: event 2's 131 Å is {base!r}, expected the replay's stored frame")
    page.focus('tr[data-ev="3"]')
    page.keyboard.press("Enter")
    if selected_event(page)["panel"] != "3":
        failures.append(f"{label}: Enter on a focused row did not select it")
    if page.evaluate("document.activeElement.dataset.ev") != "3":
        failures.append(f"{label}: focus left the row after Enter")
    page.keyboard.press("ArrowDown")
    if page.evaluate("document.activeElement.dataset.ev") != "2":
        failures.append(f"{label}: ArrowDown did not move to the next row")
    # A link to an event.
    page.goto(base_url + "?evento=2")
    page.wait_for_load_state("networkidle")
    sel = selected_event(page)
    if sel["row"] != "2" or sel["panel"] != "2":
        failures.append(f"{label}: ?evento=2 opened on {sel}")
    page.wait_for_timeout(300)
    page.locator("[data-flare-events]").screenshot(path=str(out / f"{label}-evento.png"))


def check_loop(page, failures, label, out, keyboard):
    """The 6-h loop: nothing loaded before play; then the frames load in
    order, 4 at a time, and playback starts once ~20 % are in, with
    "cargando N/M" shown until all are (D-110). Frames are slowed down here
    so that moment can be seen; the screenshot is taken paused mid-loop."""
    import time

    before = page.evaluate("performance.getEntriesByType('resource').filter(e => e.name.includes('/render/')).length")
    if before:
        failures.append(f"{label}: {before} loop frame(s) fetched before play was pressed")
    page.route("**/render/**", lambda route: (time.sleep(0.02), route.continue_()))
    page.click("#flare-loop [data-fp-start]")
    player = PLAYER.format(id="flare-loop")
    page.wait_for_function(f"{player}.playing", timeout=30000)
    early = page.evaluate(f"""({{loaded: {player}.loaded, n: {player}.frames.length,
        settled: {player}.frames.filter(f => f.settled).length,
        text: document.querySelector('#flare-loop [data-fp-progress-text]').textContent,
        shown: !document.querySelector('#flare-loop [data-fp-progress]').hidden,
        frame: !document.getElementById('flare-loop-frame').hidden}})""")
    if early["loaded"] or not early["shown"] or not re.fullmatch(r"cargando \d+/\d+", early["text"]) \
            or not early["frame"]:
        failures.append(f"{label}: at the start of playback {early} (expected still loading, 'cargando N/M')")
    page.wait_for_function(f"{player}.loaded", timeout=60000)
    page.unroute("**/render/**")
    n = page.evaluate(f"{player}.frames.length")
    ok = page.evaluate(f"{player}.frames.filter(f => f.ok).length")
    if n < 100 or ok != n:
        failures.append(f"{label}: loop {ok}/{n} frames loaded")
    if not page.evaluate("document.querySelector('#flare-loop [data-fp-progress]').hidden"):
        failures.append(f"{label}: 'cargando' still shown once every frame is in")
    print(f"{label}: loop {n} frames, playing after {page.evaluate(player + '.timing.first')} ms, "
          f"all in after {page.evaluate(player + '.timing.all')} ms (stub, 20 ms per frame)")
    page.evaluate(f"{player}.pause(); {player}.show(Math.floor({player}.frames.length / 2))")
    if keyboard:
        page.focus("#flare-loop [data-fp-slider]")
        before = page.evaluate(f"{player}.index")
        page.keyboard.press("ArrowRight")
        if page.evaluate(f"{player}.index") != before + 1:
            failures.append(f"{label}: ArrowRight on the slider did not step a frame")
        page.click("#flare-loop [data-fp-play]")
        page.wait_for_timeout(300)
        page.keyboard.press("k")
        if page.evaluate(f"{player}.playing"):
            failures.append(f"{label}: K did not pause the player")
        page.evaluate(f"{player}.show(Math.floor({player}.frames.length / 2))")
    label_text = page.evaluate("document.querySelector('#flare-loop [data-fp-label]').textContent")
    if " UT" not in label_text or "prob." not in label_text:
        failures.append(f"{label}: frame label {label_text!r}")
    page.locator(".flare-images").screenshot(path=str(out / f"{label}-loop.png"))


def check_replay(page, failures, label, out):
    """A replay, in the selected event's panel (D-107): 171 Å frames with the
    heat map on alert frames, the event's marks on the slider, the X-ray
    chart with its cursor; closing it brings back the Grad-CAM still."""
    page.evaluate("document.querySelector('[data-flare-ev]').__swxSelect(3)")
    page.click("#flare-ev [data-replay]")
    player = PLAYER.format(id="flare-replay-player")
    page.wait_for_function(f"{player}.frames.length > 0 && {player}.loaded", timeout=30000)
    page.wait_for_function(COUNT_READY_STACKS + " >= 2", timeout=20000)
    page.wait_for_load_state("networkidle")
    peak = page.evaluate(f"{player}.nearest({player}.data.marks.peak)")
    page.evaluate(f"{player}.pause(); {player}.show({peak})")
    state = page.evaluate("""({heat: !document.querySelector('#flare-ev-frame .fp-heat').hidden,
        playing: document.getElementById('flare-ev-frame').classList.contains('is-playing'),
        marks: document.querySelectorAll('#flare-ev .fp-mark').length,
        cursor: document.querySelector('.flare-replay-xray').__swxStack.ec.getOption().series
                  .some(s => s.id === 'swx-cursor')})""")
    if not state["heat"] or not state["playing"] or state["marks"] != 3 or not state["cursor"]:
        failures.append(f"{label}: replay at its peak {state} (in the panel's frame, heat map, 3 marks, X-ray cursor)")
    page.wait_for_timeout(200)
    page.locator("#flare-ev").screenshot(path=str(out / f"{label}-replay.png"))
    page.click("#flare-ev [data-replay-close]")
    if page.evaluate("document.getElementById('flare-ev-frame').classList.contains('is-playing')"):
        failures.append(f"{label}: closing the replay left it playing in the panel")


SUN_ORDER = ["131", "171", "304", "1600"]


def check_sun_now(page, failures, label, interactive):
    """D-104/D-106: the home page's "El Sol ahora": the large image loaded
    with the page, four thumbnails in a fixed order with 171 Å selected, and
    (once) a click that selects another without reordering them."""
    if not page.evaluate("!!document.querySelector('[data-sun-now]:not([hidden])')"):
        failures.append(f"{label}: no 'El Sol ahora' block")
        return
    page.locator("[data-sun-now]").scroll_into_view_if_needed()
    loaded = wait_images(page, "[data-sun-now] img")
    if not all(loaded) or len(loaded) != 5:
        failures.append(f"{label}: Sun block images loaded {loaded}")
    thumbs = "[...document.querySelectorAll('[data-sun-thumb]')].map(b => b.dataset.wave + (b.getAttribute('aria-pressed') === 'true' ? '*' : ''))"
    if page.evaluate(thumbs) != ["131", "171*", "304", "1600"]:
        failures.append(f"{label}: thumbnails {page.evaluate(thumbs)}, expected 131, 171 (selected), 304, 1600")
    if interactive:
        page.click('[data-sun-thumb][data-wave="304"]')
        main = page.evaluate("document.querySelector('[data-sun-main]').dataset.wave")
        after = page.evaluate(thumbs)
        if main != "304" or after != ["131", "171", "304*", "1600"]:
            failures.append(f"{label}: after a click on 304 the large image is {main!r}, thumbnails {after}")
        page.click('[data-sun-thumb][data-wave="171"]')


def check_offer_card(page, failures, label):
    """D-109: the flare detector's card is clickable as a whole."""
    page.locator(".ocard.has-link").first.scroll_into_view_if_needed()
    target = page.evaluate("""(() => {
        const card = [...document.querySelectorAll('.ocard')].find(c => c.textContent.includes('fulguraciones'));
        if (!card) return null;
        const r = card.querySelector('p').getBoundingClientRect();
        const a = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return a && a.closest('a') ? a.closest('a').getAttribute('href') : null;
    })()""")
    if not target or not target.endswith("/productos/usach.flare.nrt/"):
        failures.append(f"{label}: a click on the flare card's text reaches {target!r}, not the flare page")


def check_code_blocks(page, failures, label):
    # Every code example has visible text (a <pre> whose text collapses to
    # zero height would look like an empty grey bar).
    heights = page.evaluate("[...document.querySelectorAll('pre.code')].map(e => e.clientHeight)")
    if not heights or min(heights) < 30:
        failures.append(f"{label}: code examples not rendered (heights {heights})")


def wait_for_charts(page, expected):
    page.wait_for_function(f"{COUNT_READY_STACKS} >= {expected}", timeout=20000)
    page.wait_for_load_state("networkidle")


def check_zoom_reset(page, failures, label):
    stack_ref = "document.querySelector('.swx-stack').__swxStack"
    page.evaluate(f"{stack_ref}.ec.dispatchAction({{type: 'dataZoom', start: 20, end: 40}})")
    page.click("[data-reset-zoom]")
    page.wait_for_timeout(200)
    dz = page.evaluate(f"{stack_ref}.ec.getOption().dataZoom[0]")
    if not (dz["start"] == 0 and dz["end"] == 100):
        failures.append(f"{label}: 'Restablecer zoom' left dataZoom at {dz['start']}-{dz['end']}, not 0-100")


def check_range_race(page, failures, label):
    requests = []
    page.on("request", lambda r: requests.append(r.url) if "serie.json" in r.url else None)
    row = page.locator("[data-range-row]").first
    row.locator('[data-chart-range="7d"]').click(force=True)
    row.locator('[data-chart-range="6h"]').click(force=True)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    span = page.evaluate(SPAN_SECONDS)
    if span > 6 * 3600 + 120:
        failures.append(f"{label}: after a fast 7d->6h click the chart shows {span / 3600:.1f} h, not 6 h (B1)")
    requests.clear()
    row.locator('[data-chart-range="7d"]').click(force=True)  # aborted above: fetched now
    page.wait_for_load_state("networkidle")
    row.locator('[data-chart-range="6h"]').click(force=True)  # loaded above: cached
    page.wait_for_timeout(300)
    sixh = [u for u in requests if "range=6h" in u]
    if sixh:
        failures.append(f"{label}: going back to 6h refetched {len(sixh)} serie.json (B2 cache miss)")


def check_forecast_views(page, failures, label):
    """The lead selector switches to the verification view and back, and
    'reset zoom' works on the forecast chart too."""
    fc = "document.querySelector('.swx-forecast').__swxForecast"
    page.click('[data-fc-lead="3h"]')
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    view = page.evaluate(f"{fc}.data && {fc}.data.view")
    pairs = page.evaluate(f"{fc}.data && {fc}.data.pairs ? {fc}.data.pairs.valid_at.length : 0")
    if view != "lead" or not pairs:
        failures.append(f"{label}: the 3 h lead button showed view={view!r} with {pairs} pair(s)")
    page.click('[data-fc-view="issue"]')
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    if page.evaluate(f"{fc}.data && {fc}.data.view") != "issue":
        failures.append(f"{label}: 'Último pronóstico' did not return to the issue view")
    page.evaluate(f"{fc}.ec.dispatchAction({{type: 'dataZoom', start: 20, end: 40}})")
    page.click("[data-reset-zoom]")
    page.wait_for_timeout(200)
    dz = page.evaluate(f"{fc}.ec.getOption().dataZoom[0]")
    if not (dz["start"] == 0 and dz["end"] == 100):
        failures.append(f"{label}: 'Restablecer zoom' left dataZoom at {dz['start']}-{dz['end']}, not 0-100")


QUIET_PAGES = {
    # v0.6.1: the same page with the stub switched to its quiet scenario
    # (the workflow does that and collects again, then runs --quiet-flare).
    "usach.flare.nrt-quiet": ("/productos/usach.flare.nrt/", 1, "flare-quiet"),
}


def main(base_url, out_dir, mode=None):
    global PAGES
    if mode == "--quiet-flare":
        PAGES = QUIET_PAGES
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for page_name, (path, expected, checks) in PAGES.items():
            for lang, prefix in LANGS.items():
                for theme in THEMES:
                    for size, width in WIDTHS.items():
                        label = f"{page_name}-{lang}-{theme}-{size}"
                        context = browser.new_context(
                            viewport={"width": width, "height": 900}, locale=BROWSER_LOCALES[lang],
                            # A Chilean visitor: proves the axis is UT, not browser-local time.
                            timezone_id="America/Santiago",
                        )
                        context.add_init_script(f"try {{ localStorage.setItem('theme', '{theme}') }} catch (e) {{}}")
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda exc, errors=errors: errors.append(str(exc)))
                        page.on("console", lambda m, errors=errors: errors.append(m.text) if m.type == "error" else None)
                        page.goto(base_url.rstrip("/") + prefix + path)
                        try:
                            wait_for_charts(page, expected)
                        except Exception as exc:  # noqa: BLE001 — reported below with the counts
                            failures.append(f"{label}: charts never finished rendering ({exc.__class__.__name__})")
                        count = page.evaluate(COUNT_READY_STACKS)
                        if count != expected:
                            failures.append(f"{label}: {count} chart stack(s) rendered, expected {expected}")
                        statuses = page.evaluate(VISIBLE_STATUS)
                        if statuses:
                            failures.append(f"{label}: panel status visible: {statuses}")
                        if errors:
                            failures.append(f"{label}: JS errors: {errors}")
                        if checks != "code":
                            try:
                                check_local_time(page, failures, label, lang)
                            except Exception as exc:  # noqa: BLE001
                                failures.append(f"{label}: local time check failed ({exc})")
                        if page_name == "home":
                            first = lang == "es" and theme == "day" and size == "desktop"
                            check_sun_now(page, failures, label, interactive=first)
                            if first:
                                check_offer_card(page, failures, label)
                        page.screenshot(path=str(out / f"{label}.png"), full_page=True)
                        if lang == "es" and theme == "day" and size == "desktop":
                            if checks == "stack":
                                check_zoom_reset(page, failures, label)
                                check_range_race(page, failures, label)
                            elif checks == "forecast":
                                check_forecast_views(page, failures, label)
                            elif checks == "delayed":
                                check_delayed(page, failures, label)
                        if checks == "code":
                            check_code_blocks(page, failures, label)
                        if checks == "daily":
                            check_daily(page, failures, label, lang)
                        if checks in ("flare", "flare-quiet"):
                            first = lang == "es" and theme == "day" and size == "desktop"
                            check_flare(page, failures, label, lang, interactive=first, quiet=checks == "flare-quiet")
                            if first:
                                check_local_switch(page, failures, label)
                            if lang == "es" and theme == "day":
                                # v0.6.2: the table as the selector, ?evento=, an
                                # event selected (desktop and phone).
                                check_events(page, failures, label, base_url.rstrip("/") + prefix + path, out,
                                             quiet=checks == "flare-quiet")
                            if checks == "flare" and lang == "es":
                                check_loop(page, failures, label, out, keyboard=first)
                                check_replay(page, failures, label, out)
                        context.close()
        browser.close()
    for f in failures:
        print("FAIL", f)
    print(f"{len(list(out.glob('*.png')))} screenshots in {out}, {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
