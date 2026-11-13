"""How long the flare page's 6-h loop takes to start and to load (D-110).

    python .github/scripts/loop_timing.py https://helio.fisica.usach.cl/clima-espacial/productos/usach.flare.nrt/ \
        [--runs 3] [--net 20,40]

Opens the page in a fresh browser (empty browser cache), presses "Ver las
últimas 6 h", and reports when playback started and when the last frame was
in (the player's own `timing`, ms from the press), then presses it again in
the same browser (browser cache warm). The first run after a new slot or a
cache flush is what a first visitor gets with nginx's cache cold; run it
twice in a row to see nginx's cache warm. `--net MBIT,RTT_MS` emulates a
slower connection (Chromium's network emulation). `--width 390` is a phone's
viewport (the player keeps a smaller decoded window under 700 px, D-111).
Memory (D-111): after the last frame is in it plays `--passes` more passes
at 20 fps and reports the player's own peak of decoded frames (exact: width
x height x 4 bytes each) and, on Linux, the peak growth of the browser's
processes (PSS summed over Chromium's processes, sampled every 0.2 s, from
before the press). Needs Playwright
(`pip install playwright==1.55.0 && python -m playwright install chromium`),
like screenshots.py; nothing in the app imports it.
"""

import argparse
import os
import statistics
import threading

from playwright.sync_api import sync_playwright

PLAYER = "document.getElementById('flare-loop').__swxPlayer"


def _children(root):
    """Every descendant pid of `root` (Linux /proc)."""
    parent = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as f:
                    parent[int(d)] = int(f.read().rsplit(")", 1)[1].split()[1])
            except OSError:
                pass
    out, todo = [], [root]
    while todo:
        p = todo.pop()
        kids = [c for c, pp in parent.items() if pp == p]
        out += kids
        todo += kids
    return out


def browser_pss():
    """PSS (bytes) summed over this process's descendants; None off Linux."""
    if not os.path.isdir("/proc"):
        return None
    total = 0
    for pid in _children(os.getpid()):
        try:
            with open(f"/proc/{pid}/smaps_rollup") as f:
                for line in f:
                    if line.startswith("Pss:"):
                        total += int(line.split()[1]) * 1024
        except OSError:
            pass
    return total


class PeakSampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.base = browser_pss()
        self.peak = self.base
        self.done = threading.Event()

    def run(self):
        while not self.done.wait(0.2):
            v = browser_pss()
            if v is not None and (self.peak is None or v > self.peak):
                self.peak = v

    def growth(self):
        self.done.set()
        self.join()
        return None if self.base is None else self.peak - self.base


def play(ctx, url, net, passes=0):
    page = ctx.new_page()
    if net:
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")
        rate = net[0] * 1e6 / 8
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": net[1],
                                                      "downloadThroughput": rate, "uploadThroughput": rate})
    page.goto(url)
    page.wait_for_load_state("networkidle")
    sampler = PeakSampler() if passes else None
    if sampler:
        sampler.start()
    page.click("#flare-loop [data-fp-start]")
    page.wait_for_function(f"{PLAYER} && {PLAYER}.loaded", timeout=300_000)
    timing = page.evaluate(f"{PLAYER}.timing")
    frames = page.evaluate(f"{PLAYER}.frames.length")
    mem = None
    if passes:
        page.select_option("#flare-loop [data-fp-speed]", "20")
        page.evaluate(f"{PLAYER}.play()")
        page.wait_for_timeout(int(passes * frames / 20 * 1000))
        stats = page.evaluate(f"{PLAYER}.stats")
        mem = {"decoded_mb": stats["peakBytes"] / 1e6, "decoded_frames": stats["peakFrames"],
               "window": stats["window"], "browser_mb": None}
        growth = sampler.growth()
        if growth is not None:
            mem["browser_mb"] = growth / 1e6
    page.close()
    return timing["first"], timing["all"], frames, mem


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--net", help="MBIT,RTT_MS, e.g. 20,40")
    ap.add_argument("--width", type=int, default=1366, help="viewport width (390: a phone)")
    ap.add_argument("--passes", type=int, default=0, help="passes to play after loading, for memory (0: none)")
    args = ap.parse_args()
    net = tuple(float(v) for v in args.net.split(",")) if args.net else None
    cold, warm = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for _ in range(args.runs):
            ctx = browser.new_context(viewport={"width": args.width, "height": 900})
            cold.append(play(ctx, args.url, net, args.passes))
            warm.append(play(ctx, args.url, net))
            ctx.close()
        browser.close()
    for name, runs in (("browser cache empty", cold), ("browser cache warm", warm)):
        print(f"{name:20} frames {runs[0][2]:4}  playing after {statistics.median(r[0] for r in runs):6.0f} ms"
              f"  all in {statistics.median(r[1] for r in runs):6.0f} ms  runs {[r[:2] for r in runs]}")
    mems = [r[3] for r in cold if r[3]]
    if mems:
        browser = [m["browser_mb"] for m in mems if m["browser_mb"] is not None]
        print(f"memory, width {args.width}: window {mems[0]['window']}, decoded peak "
              f"{max(m['decoded_frames'] for m in mems)} frames = {max(m['decoded_mb'] for m in mems):.1f} MB"
              + (f"; browser processes grew by {statistics.median(browser):.0f} MB (median PSS peak)" if browser else ""))


if __name__ == "__main__":
    main()
