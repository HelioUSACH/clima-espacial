"""Release screenshots + browser smoke checks (18-HANDOFF-v0.2.0.md §3;
supersedes the uPlot-era version, D-058).

    python .github/scripts/screenshots.py http://127.0.0.1:8000 shots/

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


def main(base_url, out_dir):
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
                        context.close()
        browser.close()
    for f in failures:
        print("FAIL", f)
    print(f"{len(list(out.glob('*.png')))} screenshots in {out}, {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
