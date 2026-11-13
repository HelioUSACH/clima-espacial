"""How long the flare page's 6-h loop takes to start and to load (D-110).

    python .github/scripts/loop_timing.py https://helio.fisica.usach.cl/clima-espacial/productos/usach.flare.nrt/ \
        [--runs 3] [--net 20,40]

Opens the page in a fresh browser (empty browser cache), presses "Ver las
últimas 6 h", and reports when playback started and when the last frame was
in (the player's own `timing`, ms from the press), then presses it again in
the same browser (browser cache warm). The first run after a new slot or a
cache flush is what a first visitor gets with nginx's cache cold; run it
twice in a row to see nginx's cache warm. `--net MBIT,RTT_MS` emulates a
slower connection (Chromium's network emulation). Needs Playwright
(`pip install playwright==1.55.0 && python -m playwright install chromium`),
like screenshots.py; nothing in the app imports it.
"""

import argparse
import statistics

from playwright.sync_api import sync_playwright

PLAYER = "document.getElementById('flare-loop').__swxPlayer"


def play(ctx, url, net):
    page = ctx.new_page()
    if net:
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")
        rate = net[0] * 1e6 / 8
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": net[1],
                                                      "downloadThroughput": rate, "uploadThroughput": rate})
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.click("#flare-loop [data-fp-start]")
    page.wait_for_function(f"{PLAYER} && {PLAYER}.loaded", timeout=300_000)
    timing = page.evaluate(f"{PLAYER}.timing")
    frames = page.evaluate(f"{PLAYER}.frames.length")
    page.close()
    return timing["first"], timing["all"], frames


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--net", help="MBIT,RTT_MS, e.g. 20,40")
    args = ap.parse_args()
    net = tuple(float(v) for v in args.net.split(",")) if args.net else None
    cold, warm = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for _ in range(args.runs):
            ctx = browser.new_context()
            cold.append(play(ctx, args.url, net))
            warm.append(play(ctx, args.url, net))
            ctx.close()
        browser.close()
    for name, runs in (("browser cache empty", cold), ("browser cache warm", warm)):
        print(f"{name:20} frames {runs[0][2]:4}  playing after {statistics.median(r[0] for r in runs):6.0f} ms"
              f"  all in {statistics.median(r[1] for r in runs):6.0f} ms  runs {[r[:2] for r in runs]}")


if __name__ == "__main__":
    main()
