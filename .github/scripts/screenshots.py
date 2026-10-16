"""Release screenshots + browser smoke checks (13-AUDIT §5 B.2, D-058).

    python .github/scripts/screenshots.py http://127.0.0.1:8000 shots/

Against a running portal with seeded data (`manage.py seed_dev_data`), for
home and one product page × 1440/390 px × day/night × ES/EN:

- waits for every chart to render and saves a full-page screenshot;
- fails if the number of rendered charts isn't the expected one, if any
  panel shows its empty/error status, or if the page logs a JS error;
- once per page (ES, day, desktop): checks the range-click race fix
  (16-PERF B1) — a fast 7d→6h double click must end up showing 6 h of
  data, not whichever response arrived last — and that the second visit to
  a range is served from the client cache without a request (B2).

Exit status 1 on any failure, with every failure listed.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = {
    # name: (path, expected chart count)
    "home": ("/", 9),
    "noaa.rtsw.mag": ("/productos/noaa.rtsw.mag/", 2),
}
WIDTHS = {"desktop": 1440, "mobile": 390}
THEMES = ("day", "night")
LANGS = {"es": "", "en": "/en"}
# An explicit browser locale: uPlot builds an Intl formatter at load time,
# and a runner whose shell locale is e.g. "en_US@posix" hands Chromium an
# invalid language tag that makes it throw before any chart exists.
BROWSER_LOCALES = {"es": "es-CL", "en": "en-US"}

COUNT_CHARTS = "document.querySelectorAll('.swx-panel__chart .uplot').length"
VISIBLE_STATUS = """
[...document.querySelectorAll('.swx-panel__status')]
  .filter(el => !el.hidden && el.offsetParent !== null)
  .map(el => el.textContent.trim())
"""
FIRST_SPAN_SECONDS = """
(() => {
  const states = document.querySelector('.swx-panel-stack').__swxPanelStates;
  const t = states[0].uplot.data[0];
  return t[t.length - 1] - t[0];
})()
"""


def wait_for_charts(page, expected):
    page.wait_for_function(
        f"{COUNT_CHARTS} >= {expected} || document.querySelectorAll('.swx-panel__status:not([hidden])').length",
        timeout=20000,
    )
    page.wait_for_load_state("networkidle")


def check_range_race(page, failures, label):
    requests = []
    page.on("request", lambda r: requests.append(r.url) if "serie.json" in r.url else None)
    row = page.locator("[data-range-row]").first
    row.locator('[data-chart-range="7d"]').click()
    row.locator('[data-chart-range="6h"]').click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    span = page.evaluate(FIRST_SPAN_SECONDS)
    if span > 6 * 3600 + 120:
        failures.append(f"{label}: after a fast 7d→6h click the chart shows {span / 3600:.1f} h, not 6 h (B1)")
    requests.clear()
    row.locator('[data-chart-range="7d"]').click()  # aborted above: fetched now
    page.wait_for_load_state("networkidle")
    row.locator('[data-chart-range="6h"]').click()  # loaded above: cached
    page.wait_for_timeout(300)
    sixh = [u for u in requests if "range=6h" in u]
    if sixh:
        failures.append(f"{label}: going back to 6h refetched {len(sixh)} serie.json (B2 cache miss)")


def main(base_url, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for page_name, (path, expected) in PAGES.items():
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
                        count = page.evaluate(COUNT_CHARTS)
                        if count != expected:
                            failures.append(f"{label}: {count} charts rendered, expected {expected}")
                        statuses = page.evaluate(VISIBLE_STATUS)
                        if statuses:
                            failures.append(f"{label}: panel status visible: {statuses}")
                        if errors:
                            failures.append(f"{label}: JS errors: {errors}")
                        page.screenshot(path=str(out / f"{label}.png"), full_page=True)
                        if lang == "es" and theme == "day" and size == "desktop":
                            check_range_race(page, failures, label)
                        context.close()
        browser.close()
    for f in failures:
        print("FAIL", f)
    print(f"{len(list(out.glob('*.png')))} screenshots in {out}, {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
