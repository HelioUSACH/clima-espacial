"""A stand-in for flare-nrt's HTTP service (dev, CI screenshots, load tests).

    python .github/scripts/flare_nrt_stub.py [--port 5050] [--scenario active|quiet] [--delay 0]

Serves the endpoints the portal uses, in the shape of flare-nrt v1.0.0's
INTEGRATION.md ("GET /api/status", "Display endpoints"): /api/status,
/api/series, /api/events, /api/frames, /api/image/{wave}.png and
/api/gradcam/{latest|ISO}.png. Synthetic values, times relative to now.
`--delay` makes every answer wait that many seconds first (a slow or hung
service). Standard library only; nothing in the portal imports it.
"""

import argparse
import datetime as dt
import json
import math
import random
import re
import struct
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

SLOT = dt.timedelta(minutes=2)
WAVES = ("131", "171", "304", "1600")
TINTS = {"131": (0.25, 0.95, 0.85), "171": (1.0, 0.8, 0.25), "304": (1.0, 0.35, 0.15), "1600": (0.9, 0.85, 0.35)}


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def now_slot():
    now = dt.datetime.now(dt.UTC).replace(second=0, microsecond=0)
    return now - dt.timedelta(minutes=now.minute % 2) - dt.timedelta(minutes=10)  # ~10 min latency


def png(width, height, pixel):
    """A tiny RGB PNG encoder: `pixel(x, y)` -> (r, g, b) bytes 0-255."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixel(x, y))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def sun_png(wave, kind, flare_xy=None, size=256):
    rnd = random.Random(int(wave) * (2 if kind == "diff" else 1))
    tint = TINTS[wave]
    r_disk = 0.40 * size
    spots = [(rnd.uniform(-0.6, 0.6) * r_disk, rnd.uniform(-0.6, 0.6) * r_disk, rnd.uniform(8, 20)) for _ in range(6)]
    if flare_xy:
        spots.append((flare_xy[0], flare_xy[1], 6.0))

    def pixel(x, y):
        dx, dy = x - size / 2, y - size / 2
        r = math.hypot(dx, dy)
        if kind == "diff":
            v = 128
            for sx, sy, w in spots[-1:] if flare_xy else []:
                v += int(110 * math.exp(-((dx - sx) ** 2 + (dy - sy) ** 2) / (2 * w * w)))
            v += (x * 7 + y * 13) % 9 - 4
            return bytes((max(0, min(255, v)),) * 3)
        if r > r_disk * 1.08:
            return b"\x00\x00\x00"
        level = 0.35 if r > r_disk else 0.55 + 0.35 * math.sqrt(max(0.0, 1 - (r / r_disk) ** 2))
        for sx, sy, w in spots:
            level += 0.5 * math.exp(-((dx - sx) ** 2 + (dy - sy) ** 2) / (2 * w * w))
        level = min(level, 1.0) * (0.35 if r > r_disk else 1.0)
        return bytes(int(255 * level * c) for c in tint)

    return png(size, size, pixel)


def gradcam_png(strength, flare_xy=None, size=256):
    cx, cy = flare_xy or (0.0, 0.0)

    def pixel(x, y):
        dx, dy = x - size / 2 - cx, y - size / 2 - cy
        heat = strength * math.exp(-(dx * dx + dy * dy) / (2 * 30.0 ** 2))
        base = 18 if math.hypot(x - size / 2, y - size / 2) < 0.4 * size else 4
        return bytes((min(255, base + int(235 * heat)), min(255, base + int(120 * heat * heat)), base + 10))

    return png(size, size, pixel)


class World:
    """Everything the stub answers, built once per scenario."""

    def __init__(self, scenario):
        self.scenario = scenario
        self.active = scenario == "active"
        # Helioprojective centroid and box of the open event (arcsec), and
        # the same point on the 256 px stub image (north up, 9.6″/px here).
        self.centroid = [-412.3, 268.9]
        self.bbox = [-460.8, 230.4, -364.8, 307.2]
        flare_px = (self.centroid[0] / 9.6, -self.centroid[1] / 9.6) if self.active else None
        self.images = {(w, k): sun_png(w, k, flare_px) for w in WAVES for k in ("img", "diff")}
        self.gradcam = gradcam_png(1.0 if self.active else 0.08, flare_px)

    def status(self):
        slot = now_slot()
        made = slot + dt.timedelta(minutes=9, seconds=47)
        inputs = {w: iso(slot - dt.timedelta(seconds=4 + i)).replace("Z", ".070Z") for i, w in enumerate(WAVES)}
        p = 0.9312 if self.active else 0.0612
        return {
            "status": "running", "model": "flare-nrt", "model_version": "1.0.0", "weights_sha256": "stub",
            "timestamp": iso(made), "last_update": iso(made), "observation_time": iso(slot),
            "state": "active" if self.active else "quiet", "probability": p, "raw_score": p,
            "alert": self.active, "flare_detected": self.active, "event_id": 4 if self.active else None,
            "event_t_start": iso(slot - dt.timedelta(minutes=6)) if self.active else None, "threshold": 0.8,
            "location": ({"centroid_arcsec": self.centroid, "bbox_arcsec": self.bbox, "area_px": 46,
                          "area_arcsec2": 1059.8, "on_limb": False, "method": "diff131_rate_brightest_region",
                          "confidence": "experimental"} if self.active else None),
            "ensemble_scores": [p] * 5, "ensemble_std": 0.03, "lag_min": 8,
            "baseline_slots": [iso(slot - dt.timedelta(minutes=m)) for m in (8, 10, 12)], "baseline_missing": [],
            "n_baseline_used": 3, "inputs": inputs, "data_latency_s": 587, "latency_ms": 10400, "frame_count": 100,
            "loop_errors": 0, "loop_last_error": None, "loop_last_error_ts": None,
            "live_status": "live", "stale_reason": None, "seconds_since_update": 20.0, "input_age_s": 610.0,
            "stale_after_sec": 600, "max_input_age_sec": 900, "last_frame": None,
            "server_time": iso(dt.datetime.now(dt.UTC)),
        }

    def _probability(self, t, end):
        rnd = random.Random(int(t.timestamp()))
        p = 0.06 + 0.04 * math.sin(t.timestamp() / 5400) + rnd.uniform(-0.02, 0.02)
        for back in (dt.timedelta(hours=20), dt.timedelta(days=3, hours=4)):  # two past events
            minutes = (t - (end - back)).total_seconds() / 60
            if 0 <= minutes < 14:
                p = 0.84 + 0.1 * math.sin(math.pi * minutes / 14)
        if self.active and (end - t) <= dt.timedelta(minutes=6):
            p = 0.86 + 0.07 * (1 - (end - t) / dt.timedelta(minutes=6))
        return round(max(0.0, p), 4)

    def series(self, days):
        end = now_slot()
        t = end - dt.timedelta(days=days)
        t -= dt.timedelta(minutes=t.minute % 2, seconds=t.second)
        points = []
        while t <= end:
            missing = int(t.timestamp() // 120) % 97 == 0  # now and then a slot without result
            gap = (end - dt.timedelta(hours=9)) <= t < (end - dt.timedelta(hours=8, minutes=30))  # a 30 min outage
            if not gap:
                p = None if missing else self._probability(t, end)
                state = "quiet" if p is None or p < 0.8 else "active"
                points.append([iso(t), p, state, "missing" if missing else "ok"])
            t += SLOT
        return {"threshold": 0.8, "lag_min": 8, "fields": ["slot", "probability", "state", "status"], "points": points}

    def events(self, days):
        end = now_slot()
        out = []
        for i, back in enumerate((dt.timedelta(days=3, hours=4), dt.timedelta(hours=20))):
            start = end - back
            peak = start + dt.timedelta(minutes=8)
            out.append({"event_id": i + 2, "t_start": iso(start), "t_pmax": iso(peak),
                        "t_end": iso(start + dt.timedelta(minutes=12)), "p_max": 0.94, "n_alert_frames": 7,
                        "location": {"centroid_arcsec": [310.0 - 600 * i, -122.5], "confidence": "experimental"},
                        "closed": True, "gradcam": f"/api/gradcam/{iso(peak)}.png"})
        if self.active:
            start = end - dt.timedelta(minutes=6)
            out.append({"event_id": 4, "t_start": iso(start), "t_pmax": iso(end), "t_end": iso(end), "p_max": 0.9312,
                        "n_alert_frames": 4, "location": {"centroid_arcsec": self.centroid, "confidence": "experimental"},
                        "closed": False, "gradcam": None})
        return {"events": [e for e in out if (end - dt.datetime.fromisoformat(e["t_start"])).days < days]}

    def frames(self, hours):
        end = now_slot()
        out = []
        t = end - dt.timedelta(hours=hours)
        t -= dt.timedelta(minutes=t.minute % 2)
        while t <= end:
            out.append({"slot": iso(t), "status": "ok", "state": "quiet", "probability": 0.06, "alert": False,
                        "inputs": None, "images": [131, 171, 304, 1600], "gradcam": t == end})
            t += SLOT
        return {"frames": out}


def make_handler(world, delay):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if ctype == "image/png":
                self.send_header("Cache-Control", "max-age=60")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if delay:
                time.sleep(delay)
            url = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/api/status":
                    data = world.status()
                elif url.path == "/api/series":
                    data = world.series(float(q.get("days", 3)))
                elif url.path == "/api/events":
                    data = world.events(int(q.get("days", 30)))
                elif url.path == "/api/frames":
                    data = world.frames(float(q.get("hours", 6)))
                else:
                    m = re.fullmatch(r"/api/image/(131|171|304|1600)\.png", url.path)
                    if m and q.get("kind", "img") in ("img", "diff"):
                        return self._send(200, world.images[(m.group(1), q.get("kind", "img"))], "image/png")
                    if re.fullmatch(r"/api/gradcam/[^/]+\.png", url.path):
                        return self._send(200, world.gradcam, "image/png")
                    return self._send(404, b'{"detail":"Not Found"}', "application/json")
            except (ValueError, KeyError):
                return self._send(422, b'{"detail":"bad parameter"}', "application/json")
            self._send(200, json.dumps(data).encode(), "application/json")

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5050)
    ap.add_argument("--scenario", choices=("active", "quiet"), default="active")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to wait before every answer")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(World(args.scenario), args.delay))
    server.daemon_threads = True
    print(f"flare-nrt stub on http://{args.host}:{args.port} ({args.scenario}, delay {args.delay} s)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
