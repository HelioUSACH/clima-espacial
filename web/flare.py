"""The flare detector's own page (`productos/usach.flare.nrt/`, D-100/D-101).

Two sources, kept apart on purpose:

- **The product store** (`core_event`, filled by `modules/flare/`): the
  header (probability, threshold, state, image time, open event, location)
  and the four AIA images' slot and T_OBS. It is what the portal itself
  published, so it renders even when the service is down, and old events
  (manifest version 1: probability, flare_detected, location only) still
  render.
- **flare-nrt's display endpoints**, read **server-side only** by
  `service_json` below: a whitelist of four JSON endpoints and their
  parameters, a 2 s timeout, 30 s per-process cache, and `None` (the page
  says "sin datos del detector") whenever the service is slow, down or
  answers something unexpected. Browsers never talk to the service; its
  PNGs reach them through nginx's cached image locations
  (`deploy/nginx-clima-espacial.conf`, D-100), never through Django.

Image geometry (bbox overlay): flare-nrt's PNGs are 512 px, north up, made
from the 1024² synoptic images by 2x2 binning. flare-nrt converts pixels to
helioprojective arcsec with the FITS header (`flare_nrt/localize.py`) and
falls back to 2.4″/px at 1024² centred on the image; the synoptic headers
carry essentially that (AIA's 0.6″ × 4). So on the 512 px image: 4.8″/px,
disk centre at the image centre, x (solar west) to the right, y (north) up.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone
from django.utils.translation import gettext

from core.models import Event

from . import charts
from .numfmt import fmt_number

logger = logging.getLogger(__name__)

PRODUCT_ID = "usach.flare.nrt"
XRAY_PRODUCT_ID = "noaa.goes.xray"
XRAY_LONG = "0.1-0.8nm"

# --- the service's JSON, server-side only ---------------------------------------

TIMEOUT_SECONDS = 2.0
CACHE_SECONDS = 30
# A good answer is reused while a refresh is in flight in another thread,
# for at most this long; after that a slow service reads as "no data".
STALE_SECONDS = 300
MAX_BYTES = 2_000_000

# endpoint name -> (path, {parameter: (min, max)}, top-level key that must be a list).
# Only these four, only these parameters, only integers in these ranges
# (flare-nrt INTEGRATION.md "Display endpoints").
ENDPOINTS = {
    "status": ("/api/status", {}, None),
    "series": ("/api/series", {"days": (1, 14)}, "points"),
    "events": ("/api/events", {"days": (1, 400)}, "events"),
    "frames": ("/api/frames", {"hours": (1, 168)}, "frames"),
}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class NotAllowed(ValueError):
    """An endpoint or parameter outside the whitelist."""


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _validated(name: str, params: dict) -> tuple[str, dict]:
    if name not in ENDPOINTS:
        raise NotAllowed(f"endpoint {name!r} is not whitelisted")
    path, allowed, _key = ENDPOINTS[name]
    clean = {}
    for key, value in params.items():
        if key not in allowed or not isinstance(value, int) or isinstance(value, bool):
            raise NotAllowed(f"parameter {key}={value!r} is not allowed for {name}")
        lo, hi = allowed[key]
        if not lo <= value <= hi:
            raise NotAllowed(f"parameter {key}={value} is outside {lo}..{hi}")
        clean[key] = value
    return path, clean


def _fetch(name: str, path: str, params: dict):
    url = settings.SWX_FLARE_NRT_URL.rstrip("/") + path
    try:
        response = httpx.get(url, params=params, timeout=TIMEOUT_SECONDS, follow_redirects=False)
        if response.status_code != 200 or len(response.content) > MAX_BYTES:
            return None
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    key = ENDPOINTS[name][2]
    if not isinstance(data, dict) or (key and not isinstance(data.get(key), list)):
        return None
    return data


def service_json(name: str, **params):
    """One whitelisted flare-nrt endpoint's JSON, or None. Cached
    CACHE_SECONDS per (endpoint, parameters), failures included, so a dead
    service costs one 2 s wait per worker per 30 s, not one per visitor.
    Only one thread per process fetches a given key at a time; the others
    get the previous answer (if still recent) or None, never a wait."""
    path, clean = _validated(name, params)
    key = "swx:flare-nrt:" + name + "".join(f":{k}={v}" for k, v in sorted(clean.items()))
    entry = cache.get(key)
    now = time.monotonic()
    if entry is not None and now - entry["at"] < CACHE_SECONDS:
        return entry["data"]
    lock = _lock_for(key)
    if not lock.acquire(blocking=False):
        return entry["data"] if entry is not None and now - entry["at"] < STALE_SECONDS else None
    try:
        data = _fetch(name, path, clean)
        cache.set(key, {"data": data, "at": time.monotonic()}, STALE_SECONDS)
        if data is None:
            logger.info("flare-nrt %s unavailable", name)
        return data
    finally:
        lock.release()


# --- formatting -----------------------------------------------------------------

_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
ISO_SLOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$")
_GRADCAM_PATH_RE = re.compile(r"^/api/gradcam/(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z)\.png$")


def parse_time(value) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when.replace(tzinfo=dt.UTC) if when.tzinfo is None else when.astimezone(dt.UTC)


def iso_z(when: dt.datetime) -> str:
    return when.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_ut(when: dt.datetime | None, lang: str, *, date: bool = True, seconds: bool = False) -> str:
    if when is None:
        return "—"
    when = when.astimezone(dt.UTC)
    clock = f"{when:%H:%M:%S}" if seconds else f"{when:%H:%M}"
    if not date:
        return clock
    day = f"{when.day:02d} {_MONTHS_EN[when.month - 1]}" if lang == "en" else f"{when:%d/%m}"
    return f"{day} {clock}"


def fmt_prob(value, lang: str) -> str:
    return fmt_number(value, lang, decimals=2) if isinstance(value, (int, float)) else "—"


def fmt_age(when: dt.datetime | None, now: dt.datetime) -> str:
    if when is None:
        return ""
    minutes = max(0, int((now - when).total_seconds() // 60))
    if minutes < 120:
        return gettext("hace %(n)s min") % {"n": minutes}
    hours = minutes // 60
    if hours < 48:
        return gettext("hace %(n)s h") % {"n": hours}
    return gettext("hace %(n)s días") % {"n": hours // 24}


def _arcsec(value, lang: str) -> str:
    return fmt_number(value, lang, decimals=0) + "″"


def fmt_location(location, lang: str) -> str:
    """'(−412″, 269″)', helioprojective x (west +) and y (north +)."""
    if not isinstance(location, dict):
        return ""
    centroid = location.get("centroid_arcsec")
    if not (isinstance(centroid, list) and len(centroid) == 2
            and all(isinstance(v, (int, float)) for v in centroid)):
        return ""
    return f"({_arcsec(centroid[0], lang)}, {_arcsec(centroid[1], lang)})"


# --- state ----------------------------------------------------------------------

STATE_LABELS = {
    "quiet": ("Calma", "Quiet"),
    "onset": ("Inicio de flare", "Flare onset"),
    "active": ("Flare en curso", "Flare in progress"),
    "decay": ("Decaimiento", "Decay"),
}
# The shared severity tokens (D-062), as CSS variables (D-053).
STATE_SEVERITY = {"quiet": "ok", "onset": "strong", "active": "strong", "decay": "moderate"}


def state_badge(state: str | None, lang: str) -> dict | None:
    if state not in STATE_LABELS:
        return None
    es, en = STATE_LABELS[state]
    return {"key": state, "label": en if lang == "en" else es, "severity": STATE_SEVERITY[state]}


# --- geometry ---------------------------------------------------------------------

IMAGE_PX = 512
ARCSEC_PER_PX = 4.8  # 2.4″/px at 1024², binned 2x2 (module docstring)


def bbox_percent(location) -> dict | None:
    """`bbox_arcsec` [x0, y0, x1, y1] -> the box on the 512 px north-up
    image, as CSS percentages {left, top, width, height} (strings with a
    decimal point), clipped to the image. None without a usable box."""
    if not isinstance(location, dict):
        return None
    box = location.get("bbox_arcsec")
    if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
        return None
    x0, y0, x1, y1 = (float(v) for v in box)
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        return None
    half = IMAGE_PX / 2

    def col(x):
        return half + x / ARCSEC_PER_PX

    def row(y):
        return half - y / ARCSEC_PER_PX  # north up: +y is towards the top

    left, right = sorted((col(x0), col(x1)))
    top, bottom = sorted((row(y1), row(y0)))
    left, top = max(left, 0.0), max(top, 0.0)
    right, bottom = min(right, IMAGE_PX), min(bottom, IMAGE_PX)
    if right <= left or bottom <= top:
        return None

    def pct(v):
        # A string: the template must not localise it ("31,25%" is not CSS).
        return f"{100 * v / IMAGE_PX:.2f}"

    return {"left": pct(left), "top": pct(top), "width": pct(right - left), "height": pct(bottom - top)}


# --- GOES class during an event ----------------------------------------------------

CLASS_WINDOW_AFTER = dt.timedelta(minutes=10)


def goes_classes(windows: list[tuple[dt.datetime, dt.datetime]]) -> list[str | None]:
    """For each (start, end) window, the GOES class (e.g. "M1.2") of the
    highest 0.1–0.8 nm flux stored by the portal (`noaa.goes.xray`) in
    [start, end]; None where nothing is stored. One query for all windows."""
    if not windows:
        return []
    starts = [w[0] for w in windows]
    ends = [w[1] for w in windows]
    sql = (
        "SELECT w.i, max((ts.payload ->> 'flux')::float8) "
        "FROM unnest(%s::timestamptz[], %s::timestamptz[]) WITH ORDINALITY AS w(t0, t1, i) "
        "JOIN core_timeseries ts ON ts.product_id = %s AND ts.time_tag >= w.t0 AND ts.time_tag <= w.t1 "
        "AND ts.payload ->> 'energy' = %s AND jsonb_typeof(ts.payload -> 'flux') = 'number' "
        "AND (ts.payload ->> 'flux')::float8 > 0 "
        "GROUP BY w.i"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, [starts, ends, XRAY_PRODUCT_ID, XRAY_LONG])
        peak = dict(cursor.fetchall())
    return [charts.xray_class(peak.get(i + 1)) for i in range(len(windows))]


# --- public URLs of the service's images (served by nginx, D-100) -------------------


def media_base() -> str:
    """'/clima-espacial/productos/usach.flare.nrt': language-neutral (no /en/),
    the prefix nginx's image locations match."""
    prefix = getattr(settings, "FORCE_SCRIPT_NAME", None) or ""
    return f"{prefix.rstrip('/')}/productos/{PRODUCT_ID}"


def image_url(wave: str, kind: str, slot: str | None) -> str:
    url = f"{media_base()}/img/{wave}.png?kind={kind}"
    return url + f"&slot={slot}" if slot else url


def gradcam_url(slot: str | None) -> str:
    return f"{media_base()}/gradcam/{slot or 'latest'}.png"


def public_gradcam(service_path) -> str | None:
    """The service's `/api/gradcam/<ISO>.png` -> the public nginx path, or
    None for anything else."""
    if not isinstance(service_path, str):
        return None
    match = _GRADCAM_PATH_RE.match(service_path)
    return gradcam_url(match.group(1)) if match else None


# --- the page ---------------------------------------------------------------------

WAVES = ("131", "171", "304", "1600")


def _latest_event():
    return Event.objects.filter(product_id=PRODUCT_ID).order_by("-issued").first()


def header_context(event, lang: str, now: dt.datetime) -> dict:
    """The header, from the latest stored event. Works for a version-1 event
    (no threshold, state, observation slot or provenance): those parts are
    simply left out."""
    if event is None:
        return {"has_result": False}
    payload = event.payload or {}
    provenance = event.provenance or {}
    has_slot = bool(provenance.get("inputs"))  # v2: valid_from is the AIA slot
    location = payload.get("location") if payload.get("flare_detected") else None
    open_since = parse_time(payload.get("event_t_start")) if payload.get("flare_detected") else None
    return {
        "has_result": True,
        "probability": fmt_prob(payload.get("probability"), lang),
        "threshold": fmt_prob(payload.get("threshold"), lang) if payload.get("threshold") is not None else None,
        "state": state_badge(payload.get("state"), lang),
        "observed": fmt_ut(event.valid_from, lang) if has_slot else None,
        "result_time": None if has_slot else fmt_ut(event.issued, lang),
        "age": fmt_age(event.valid_from if has_slot else event.issued, now),
        "open_since": fmt_ut(open_since, lang, date=False) if open_since else None,
        "flare_detected": bool(payload.get("flare_detected")),
        "location": fmt_location(location, lang),
        "on_limb": bool(isinstance(location, dict) and location.get("on_limb")),
        "bbox": bbox_percent(location),
    }


def images_context(event, frames, lang: str) -> dict:
    """The four channels of the latest stored result, each with its T_OBS,
    and the newest Grad-CAM the service has. With a version-1 event (no
    slot) the images are the service's latest."""
    provenance = (event.provenance or {}) if event else {}
    inputs = provenance.get("inputs") or {}
    slot = iso_z(event.valid_from) if event is not None and inputs else None
    if slot and not ISO_SLOT_RE.match(slot):
        slot = None
    channels = []
    for wave in WAVES:
        t_obs = parse_time(inputs.get(wave))
        channels.append({
            "wave": wave,
            "img": image_url(wave, "img", slot),
            "diff": image_url(wave, "diff", slot),
            "t_obs": fmt_ut(t_obs, lang, seconds=True) if t_obs else None,
        })
    cam_slot = None
    if frames:
        with_cam = [f.get("slot") for f in frames.get("frames", []) if isinstance(f, dict) and f.get("gradcam")]
        with_cam = [s for s in with_cam if isinstance(s, str) and ISO_SLOT_RE.match(s)]
        if slot in with_cam:
            cam_slot = slot
        elif with_cam:
            cam_slot = max(with_cam)
    cam_time = parse_time(cam_slot)
    return {
        "slot": slot,
        "channels": channels,
        "gradcam": gradcam_url(cam_slot),
        "gradcam_time": fmt_ut(cam_time, lang) if cam_time else None,
    }


def history_rows(events_json, lang: str) -> list[dict]:
    """flare-nrt's `/api/events` -> table rows, newest first, with the GOES
    class the portal measured between t_start and t_end + 10 min."""
    raw = [e for e in (events_json or {}).get("events", []) if isinstance(e, dict)]
    parsed = []
    for e in raw:
        start, peak, end = parse_time(e.get("t_start")), parse_time(e.get("t_pmax")), parse_time(e.get("t_end"))
        if start is None:
            continue
        parsed.append((e, start, peak, end))
    parsed.sort(key=lambda item: item[1], reverse=True)
    windows = [(start, (end or start) + CLASS_WINDOW_AFTER) for _e, start, _p, end in parsed]
    classes = goes_classes(windows)
    rows = []
    for (e, start, peak, end), goes_class in zip(parsed, classes, strict=True):
        n_frames = e.get("n_alert_frames")
        rows.append({
            "start": fmt_ut(start, lang),
            "peak": fmt_ut(peak, lang, date=peak is not None and peak.date() != start.date()) if peak else "—",
            "end": fmt_ut(end, lang, date=end is not None and end.date() != start.date()) if end else "—",
            "open": e.get("closed") is False,
            "p_max": fmt_prob(e.get("p_max"), lang),
            "n_frames": n_frames if isinstance(n_frames, int) else "—",
            "location": fmt_location(e.get("location"), lang) or "—",
            "goes_class": goes_class or "—",
            "gradcam": public_gradcam(e.get("gradcam")),
        })
    return rows


# --- the probability series for the timeline (datos/probabilidad.json) --------------

SERIES_RANGES = {"6h": (1, 6 * 3600), "1d": (1, 86400), "3d": (3, 3 * 86400), "7d": (7, 7 * 86400)}
DEFAULT_RANGE = "1d"
SLOT_SECONDS = 120


def probability_series(range_key: str, now: dt.datetime | None = None) -> dict:
    """The stack contract of `serie.json` (web/series.py) for the
    probability per slot, plus `spans` (alert slots, [start, end) in UNIX
    seconds) and `threshold`. A slot without a result is null, and so is
    every missing slot between two results, so a gap stays a gap. An
    unreachable service gives empty lists and `"unavailable": true`."""
    days, seconds = SERIES_RANGES[range_key]
    now = now or timezone.now()
    since = (now - dt.timedelta(seconds=seconds)).timestamp()
    data = service_json("series", days=days)
    out = {"time": [], "series": {"probability": []}, "min": {"probability": []}, "max": {"probability": []},
           "source": [], "bucket_seconds": SLOT_SECONDS, "anchor": "now", "spans": [], "threshold": None,
           "unavailable": data is None}
    if data is None:
        return out
    threshold = data.get("threshold")
    threshold = float(threshold) if isinstance(threshold, (int, float)) and not isinstance(threshold, bool) else None
    out["threshold"] = threshold
    points = []
    for point in data.get("points", []):
        if not isinstance(point, list) or len(point) < 2:
            continue
        when = parse_time(point[0])
        if when is None or when.timestamp() < since:
            continue
        p = point[1] if isinstance(point[1], (int, float)) and not isinstance(point[1], bool) else None
        points.append((int(when.timestamp()), p))
    points.sort()
    times, values = [], []
    for t, p in points:
        if times and t == times[-1]:
            continue
        if times and t - times[-1] > 1.5 * SLOT_SECONDS:
            gap = times[-1] + SLOT_SECONDS
            while gap <= t - SLOT_SECONDS:
                times.append(gap)
                values.append(None)
                gap += SLOT_SECONDS
        times.append(t)
        values.append(round(p, 4) if p is not None else None)
    spans = []
    if threshold is not None:
        for t, p in zip(times, values, strict=True):
            if p is None or p < threshold:
                continue
            if spans and spans[-1][1] == t:
                spans[-1][1] = t + SLOT_SECONDS
            else:
                spans.append([t, t + SLOT_SECONDS])
    out.update(time=times, source=[None] * len(times), spans=spans)
    out["series"]["probability"] = values
    out["min"]["probability"] = values
    out["max"]["probability"] = values
    return out


def probability_series_body(range_key: str) -> str:
    cache_key = f"swx:flare-serie:v1:{range_key}"
    body = cache.get(cache_key)
    if body is None:
        body = json.dumps(probability_series(range_key), separators=(",", ":"))
        cache.set(cache_key, body, CACHE_SECONDS)
    return body


def timeline_panels(lang: str, endpoint: str, xray_endpoint: str) -> list[dict]:
    """Two panels on one shared time axis (the site's stacked style):
    probability with its threshold and alert spans, then GOES 0.1–0.8 nm from
    the portal's own `noaa.goes.xray` store, with the A–X class bands. The
    threshold line's value comes with the data (`threshold` in
    probabilidad.json, `value: null` here), so it is always the service's."""
    xray_panel = charts.PRODUCT_PANELS[XRAY_PRODUCT_ID][0]
    long_series = next(s for s in xray_panel.series if s.field == XRAY_LONG)
    en = lang == "en"
    return [
        {
            "product_id": PRODUCT_ID, "endpoint": endpoint, "key": "probability",
            "title": "Probability (flare-nrt)" if en else "Probabilidad (flare-nrt)", "unit": "",
            "kind": "line", "y": "linear",
            "series": [{"field": "probability", "label": "Probability" if en else "Probabilidad",
                        "color": charts.SLOT_COLOR_VARS[1]}],
            "zero_line": False, "ref_lines": [{"value": None, "label": gettext("umbral") + " {v}"}], "log_bands": [],
            "show_source": False, "kp_bucket": False, "envelope": False, "y_range": [0, 1], "anchor": "now",
            "caption": ("probability: one per AIA image (2 min)" if en
                        else "probabilidad: una por imagen AIA (2 min)"),
            "daily": False, "spans": True, "readout_decimals": 2,
            "empty_message": gettext("Sin datos del detector."),
        },
        {
            "product_id": XRAY_PRODUCT_ID, "endpoint": xray_endpoint, "key": "xray",
            "title": "X-rays (GOES) 0.1–0.8 nm" if en else "Rayos X (GOES) 0,1–0,8 nm", "unit": xray_panel.unit,
            "kind": "line", "y": "log",
            "series": [{"field": long_series.field, "label": long_series.label_en if en else long_series.label_es,
                        "color": charts.SLOT_COLOR_VARS[2]}],
            "zero_line": False, "ref_lines": [],
            "log_bands": [{"value": v, "label": label} for v, label in xray_panel.log_bands],
            "show_source": False, "kp_bucket": False, "envelope": False, "y_range": list(xray_panel.y_range),
            "anchor": "now", "caption": None, "caption_prefix": "X-rays:" if en else "rayos X:", "daily": False,
        },
    ]


def page_context(lang: str) -> dict:
    now = timezone.now()
    event = _latest_event()
    # In parallel: a dead or slow service costs one 2 s timeout, not two.
    # (Neither call touches the database, only the per-process cache.)
    with ThreadPoolExecutor(max_workers=2) as pool:
        events_future = pool.submit(service_json, "events", days=30)
        frames_future = pool.submit(service_json, "frames", hours=1)
        events_json, frames = events_future.result(), frames_future.result()
    return {
        "header": header_context(event, lang, now),
        "images": images_context(event, frames, lang),
        "history": history_rows(events_json, lang),
        "history_available": events_json is not None,
        "service_available": events_json is not None or frames is not None,
    }
