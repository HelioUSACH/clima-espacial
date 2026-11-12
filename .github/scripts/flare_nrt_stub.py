"""A stand-in for flare-nrt's HTTP service (dev, CI screenshots, load tests).

    python .github/scripts/flare_nrt_stub.py [--port 5050] [--scenario active|quiet] [--delay 0]

Serves the endpoints the portal uses, in the shape of flare-nrt v1.1.0's
INTEGRATION.md ("GET /api/status", "Display endpoints", "Display files"):
/api/status, /api/series, /api/events, /api/frames,
/api/image/{wave}.png (kind, slot, format, size), /api/gradcam/{latest|ISO}.png
(style=overlay|heat), /api/loop, /api/render/{ISO}/{name}.webp,
/api/events/{id}/replay and /api/events/{id}/frames/{ISO}/{name}.webp.
Synthetic values, times relative to now. The standard library has no WebP
encoder, so "webp" answers are PNG bytes sent as image/png (browsers decode
an <img> by its content). `--delay` makes every answer wait that many
seconds first (a slow or hung service). `GET /_stub/scenario?set=quiet`
(or active) switches scenario while running, for CI. Standard library only;
nothing in the portal imports it.
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


def png(width, height, pixel, alpha=False):
    """A tiny PNG encoder: `pixel(x, y)` -> (r, g, b[, a]) bytes 0-255."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixel(x, y))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def sun_png(wave, kind, flare_xy=None, size=256, phase=None):
    rnd = random.Random(int(wave) * (2 if kind == "diff" else 1))
    tint = TINTS[wave]
    r_disk = 0.40 * size
    k = size / 256
    spots = [(rnd.uniform(-0.6, 0.6) * r_disk, rnd.uniform(-0.6, 0.6) * r_disk, k * rnd.uniform(8, 20)) for _ in range(6)]
    if phase is not None:  # a loop frame: one region drifting across the disk
        spots.append(((phase - 0.5) * 1.2 * r_disk, 0.25 * r_disk, k * 7.0))
    if flare_xy:
        spots.append((flare_xy[0] * k, flare_xy[1] * k, 6.0 * k))

    def pixel(x, y):
        dx, dy = x - size / 2, y - size / 2
        r = math.hypot(dx, dy)
        if kind == "diff":
            v = 128
            for sx, sy, w in spots[-1:] if (flare_xy or phase is not None) else []:
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


INFERNO = ((0, 0, 4), (33, 12, 74), (87, 16, 110), (138, 34, 106), (188, 55, 84), (228, 90, 49),
           (249, 142, 9), (249, 203, 53), (252, 255, 164))


def heat_png(flare_xy=None, size=256):
    """style=heat: the map alone, RGBA, transparent below 5 % of its maximum,
    with the limb as a thin light-grey circle."""
    cx, cy = flare_xy or (40.0, -30.0)
    r_disk = 0.40 * size

    def pixel(x, y):
        dx, dy = x - size / 2 - cx, y - size / 2 - cy
        v = math.exp(-(dx * dx + dy * dy) / (2 * 22.0 ** 2))
        limb = abs(math.hypot(x - size / 2, y - size / 2) - r_disk) < 0.8
        if v < 0.05:
            return bytes((200, 200, 200, 209)) if limb else b"\x00\x00\x00\x00"
        pos = v * (len(INFERNO) - 1)
        i = min(int(pos), len(INFERNO) - 2)
        f = pos - i
        rgb = [int(INFERNO[i][c] + f * (INFERNO[i + 1][c] - INFERNO[i][c])) for c in range(3)]
        return bytes(rgb + [255 if v >= 0.10 else int(255 * (v - 0.05) / 0.05)])

    return png(size, size, pixel, alpha=True)


LOOP_VARIANTS = 12


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
        self.flare_px = flare_px
        self.images = {(w, k, 512): sun_png(w, k, flare_px) for w in WAVES for k in ("img", "diff")}
        self.gradcam = gradcam_png(1.0 if self.active else 0.08, flare_px)
        self.heat = heat_png(flare_px)
        self.frames_cache = {}

    def image(self, wave, kind, size):
        key = (wave, kind, size)
        if key not in self.images:  # 1024 px: a 384 px stand-in, made once
            self.images[key] = sun_png(wave, kind, self.flare_px, size=384)
        return self.images[key]

    def render(self, slot, name):
        """A loop/replay frame: one of LOOP_VARIANTS images per channel."""
        if name == "gradcam-heat":
            return self.heat
        variant = int(slot.timestamp() // 120) % LOOP_VARIANTS
        key = (name, variant)
        if key not in self.frames_cache:
            self.frames_cache[key] = sun_png("171", "diff" if name == "171-diff" else "img", None, size=192,
                                             phase=variant / (LOOP_VARIANTS - 1))
        return self.frames_cache[key]

    def status(self):
        slot = now_slot()
        # A different result time per scenario: switching scenario gives the
        # collector a new result (it de-duplicates on `timestamp`, D-096).
        made = slot + dt.timedelta(minutes=9, seconds=47 if self.active else 48)
        inputs = {w: iso(slot - dt.timedelta(seconds=4 + i)).replace("Z", ".070Z") for i, w in enumerate(WAVES)}
        p = 0.9312 if self.active else 0.0612
        return {
            "status": "running", "model": "flare-nrt", "model_version": "1.1.0", "weights_sha256": "stub",
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
                        "closed": True, "gradcam": f"/api/gradcam/{iso(peak)}.png",
                        "replay": i == 0 or self.active})
        if self.active:
            start = end - dt.timedelta(minutes=6)
            out.append({"event_id": 4, "t_start": iso(start), "t_pmax": iso(end), "t_end": iso(end), "p_max": 0.9312,
                        "n_alert_frames": 4, "location": {"centroid_arcsec": self.centroid, "confidence": "experimental"},
                        "closed": False, "gradcam": None, "replay": False})
        return {"events": [e for e in out if (end - dt.datetime.fromisoformat(e["t_start"])).days < days]}

    def event_times(self, event_id):
        end = now_slot()
        back = {2: dt.timedelta(days=3, hours=4), 3: dt.timedelta(hours=20)}.get(event_id)
        if back is None:
            return None
        start = end - back
        return start, start + dt.timedelta(minutes=8), start + dt.timedelta(minutes=12)

    def loop(self, channel, hours):
        end = now_slot()
        t = end - dt.timedelta(hours=hours)
        t -= dt.timedelta(minutes=t.minute % 2, seconds=t.second)
        out = []
        while t <= end:
            if int(t.timestamp() // 120) % 41 != 0:  # now and then a slot without its file
                p = self._probability(t, end)
                out.append({"slot": iso(t), "t_obs": iso(t + dt.timedelta(seconds=10)).replace("Z", ".350Z"),
                            "url": f"/api/render/{iso(t)}/{channel}.webp", "probability": p,
                            "state": "active" if p >= 0.8 else "quiet"})
            t += SLOT
        return {"channel": channel, "hours": hours, "frames": out}

    def replay(self, event_id):
        times = self.event_times(event_id)
        if times is None or (event_id == 2 and not self.active):
            return None
        start, peak, end_ = times
        w0, w1 = start - dt.timedelta(minutes=30), end_ + dt.timedelta(minutes=60)
        frames, missing = [], []
        t = w0
        while t <= w1:
            p = round(0.07 + 0.88 * math.exp(-((t - peak).total_seconds() / 300) ** 2), 4)
            if event_id == 2 and t == peak + dt.timedelta(minutes=40):
                missing.append(iso(t))  # incomplete: one slot lost
            else:
                frames.append({"slot": iso(t), "t_obs": iso(t + dt.timedelta(seconds=10)).replace("Z", ".348Z"),
                               "probability": p, "state": "active" if p >= 0.8 else "quiet", "status": "ok",
                               "images": ["171", "gradcam-heat"] if p >= 0.8 else ["171"]})
            t += SLOT
        return {
            "schema": "flare-nrt-replay/1",
            "event": {"event_id": event_id, "t_start": iso(start), "t_peak": iso(peak), "t_end": iso(end_),
                      "p_max": 0.95, "n_alert_frames": 7, "location": {"centroid_arcsec": [310.0, -122.5]}},
            "window": {"start": iso(w0), "end": iso(w1), "pre_min": 30, "post_min": 60},
            "n_slots": len(frames) + len(missing), "frames": frames,
            "complete": not missing, "missing": {"171": missing, "gradcam-heat": []},
            "note": (f"{len(missing)} of {len(frames) + len(missing)} slots have no 171 Å image and 0 results "
                     "have no Grad-CAM: their files were gone or never made") if missing else None,
            "built_at": iso(w1 + dt.timedelta(minutes=70)), "model_version": "1.1.0",
        }

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


def make_handler(state, delay):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype, cache=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if cache:
                self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def _image(self, body, immutable=False):
            return self._send(200, body, "image/png", "public, max-age=31536000, immutable" if immutable else "max-age=60")

        def do_GET(self):
            if delay:
                time.sleep(delay)
            world = state["world"]
            url = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            iso_re = r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z)"
            try:
                if url.path == "/_stub/scenario" and q.get("set") in ("active", "quiet"):
                    state["world"] = World(q["set"])
                    data = {"scenario": q["set"]}
                elif url.path == "/api/status":
                    data = world.status()
                elif url.path == "/api/series":
                    data = world.series(float(q.get("days", 3)))
                elif url.path == "/api/events":
                    data = world.events(int(q.get("days", 30)))
                elif url.path == "/api/frames":
                    data = world.frames(float(q.get("hours", 6)))
                elif url.path == "/api/loop":
                    hours = int(q.get("hours", 6))
                    if q.get("channel") not in ("171", "171-diff", "gradcam-heat") or not 1 <= hours <= 24:
                        raise ValueError
                    data = world.loop(q["channel"], hours)
                else:
                    m = re.fullmatch(r"/api/image/(131|171|304|1600)\.png", url.path)
                    if m:
                        kind, size = q.get("kind", "img"), int(q.get("size", 512))
                        if kind not in ("img", "diff") or size not in (512, 1024) or (size == 1024 and kind == "diff") \
                                or q.get("format", "png") not in ("png", "webp"):
                            raise ValueError
                        return self._image(world.image(m.group(1), kind, size), immutable="slot" in q)
                    if re.fullmatch(r"/api/gradcam/[^/]+\.png", url.path):
                        if q.get("style", "overlay") not in ("overlay", "heat"):
                            raise ValueError
                        return self._image(world.heat if q.get("style") == "heat" else world.gradcam)
                    m = re.fullmatch(rf"/api/render/{iso_re}/(171|171-diff|gradcam-heat)\.webp", url.path)
                    if m:
                        slot = dt.datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
                        return self._image(world.render(slot, m.group(2)), immutable=True)
                    m = re.fullmatch(r"/api/events/(\d+)/replay", url.path)
                    if m:
                        data = world.replay(int(m.group(1)))
                        if data is None:
                            return self._send(404, b'{"detail":"no replay"}', "application/json")
                        return self._send(200, json.dumps(data).encode(), "application/json", "max-age=3600")
                    m = re.fullmatch(rf"/api/events/(\d+)/frames/{iso_re}/(171|gradcam-heat)\.webp", url.path)
                    if m and world.replay(int(m.group(1))) is not None:
                        slot = dt.datetime.fromisoformat(m.group(2).replace("Z", "+00:00"))
                        return self._image(world.render(slot, m.group(3)), immutable=True)
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
    server = ThreadingHTTPServer((args.host, args.port), make_handler({"world": World(args.scenario)}, args.delay))
    server.daemon_threads = True
    print(f"flare-nrt stub on http://{args.host}:{args.port} ({args.scenario}, delay {args.delay} s)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
