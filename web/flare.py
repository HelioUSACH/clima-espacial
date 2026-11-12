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

import httpx
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone
from django.utils.translation import gettext

from core.models import Event

from . import charts
from .numfmt import fmt_number
from .timefmt import time_html

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

# endpoint name -> (path, {parameter: rule}, top-level key that must be a list).
# Only these endpoints, only these parameters (flare-nrt INTEGRATION.md
# "Display endpoints" and "Display files (1.1.0)"). A rule is (min, max) for
# an integer or a frozenset of allowed strings. A parameter named in the
# path ("{event_id}") goes into the path, never the query.
LOOP_CHANNELS = frozenset({"171", "171-diff", "gradcam-heat"})
ENDPOINTS = {
    "status": ("/api/status", {}, None),
    "series": ("/api/series", {"days": (1, 14)}, "points"),
    "events": ("/api/events", {"days": (1, 400)}, "events"),
    "frames": ("/api/frames", {"hours": (1, 168)}, "frames"),
    "loop": ("/api/loop", {"channel": LOOP_CHANNELS, "hours": (1, 24)}, "frames"),
    "replay": ("/api/events/{event_id}/replay", {"event_id": (1, 999_999_999)}, "frames"),
}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class NotAllowed(ValueError):
    """An endpoint or parameter outside the whitelist."""


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _validated(name: str, params: dict) -> tuple[str, dict]:
    """(path with its path parameters filled in, query parameters)."""
    if name not in ENDPOINTS:
        raise NotAllowed(f"endpoint {name!r} is not whitelisted")
    path, allowed, _key = ENDPOINTS[name]
    clean = {}
    for key, value in params.items():
        rule = allowed.get(key)
        if isinstance(rule, frozenset):
            if not isinstance(value, str) or value not in rule:
                raise NotAllowed(f"parameter {key}={value!r} is not allowed for {name}")
        elif rule is None or not isinstance(value, int) or isinstance(value, bool):
            raise NotAllowed(f"parameter {key}={value!r} is not allowed for {name}")
        else:
            lo, hi = rule
            if not lo <= value <= hi:
                raise NotAllowed(f"parameter {key}={value} is outside {lo}..{hi}")
        clean[key] = value
    for key in re.findall(r"\{(\w+)\}", path):
        if key not in clean:
            raise NotAllowed(f"parameter {key} is required for {name}")
        path = path.replace("{" + key + "}", str(clean.pop(key)))
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
    key = "swx:flare-nrt:" + path + "".join(f":{k}={v}" for k, v in sorted(clean.items()))
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


# --- public URLs of the service's images (served by nginx, D-100, D-103) -------------

_RENDER_PATH_RE = re.compile(r"^/api/render/(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z)/(171|171-diff|gradcam-heat)\.webp$")


def media_base() -> str:
    """'/clima-espacial/productos/usach.flare.nrt': language-neutral (no /en/),
    the prefix nginx's image locations match."""
    prefix = getattr(settings, "FORCE_SCRIPT_NAME", None) or ""
    return f"{prefix.rstrip('/')}/productos/{PRODUCT_ID}"


def image_url(wave: str, kind: str, slot: str | None, *, fmt: str | None = None, size: int | None = None) -> str:
    """img/<wave>.png?kind=…[&slot=…][&format=webp][&size=1024]. The path
    keeps '.png' (nginx's location); `format` says what comes back."""
    url = f"{media_base()}/img/{wave}.png?kind={kind}"
    if slot:
        url += f"&slot={slot}"
    if fmt:
        url += f"&format={fmt}"
    if size:
        url += f"&size={size}"
    return url


def gradcam_url(slot: str | None, *, style: str | None = None, fmt: str | None = None) -> str:
    url = f"{media_base()}/gradcam/{slot or 'latest'}.png"
    query = "&".join(f"{k}={v}" for k, v in (("style", style), ("format", fmt)) if v)
    return url + ("?" + query if query else "")


def public_gradcam(service_path) -> str | None:
    """The service's `/api/gradcam/<ISO>.png` -> the public nginx path, or
    None for anything else."""
    if not isinstance(service_path, str):
        return None
    match = _GRADCAM_PATH_RE.match(service_path)
    return gradcam_url(match.group(1)) if match else None


def public_render(service_path, channel: str) -> str | None:
    """The service's `/api/render/<ISO>/<channel>.webp` (a loop frame's
    `url`) -> the public nginx path; None for anything else, including a
    frame of another channel."""
    if not isinstance(service_path, str):
        return None
    match = _RENDER_PATH_RE.match(service_path)
    if not match or match.group(2) != channel:
        return None
    return f"{media_base()}/render/{match.group(1)}/{channel}.webp"


def replay_frame_url(event_id: int, slot: str, name: str) -> str:
    """One replay frame's public path (the service's
    /api/events/<id>/frames/<ISO>/<name>.webp, through nginx)."""
    return f"{media_base()}/replay/{int(event_id)}/{slot}/{name}.webp"


# --- the page ---------------------------------------------------------------------

WAVES = ("131", "171", "304", "1600")
DEFAULT_WAVE = "171"
DEFAULT_THRESHOLD = 0.8  # flare-nrt's own (INTEGRATION.md), when nothing better is known
LARGE_PX = 1024


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _latest_event():
    return Event.objects.filter(product_id=PRODUCT_ID).order_by("-issued").first()


def stored_threshold(event) -> float:
    value = _number((event.payload or {}).get("threshold")) if event is not None else None
    return value if value is not None else DEFAULT_THRESHOLD


def _event_slot(event) -> str | None:
    """The stored result's AIA slot (version-2 events only: v1 has none)."""
    if event is None or not (event.provenance or {}).get("inputs"):
        return None
    slot = iso_z(event.valid_from)
    return slot if ISO_SLOT_RE.match(slot) else None


def header_context(event, lang: str, now: dt.datetime) -> dict:
    """The header, from the latest stored event. Works for a version-1 event
    (no threshold, state, observation slot or provenance): those parts are
    simply left out."""
    if event is None:
        return {"has_result": False}
    payload = event.payload or {}
    has_slot = _event_slot(event) is not None
    location = payload.get("location") if payload.get("flare_detected") else None
    open_since = parse_time(payload.get("event_t_start")) if payload.get("flare_detected") else None
    return {
        "has_result": True,
        "probability": fmt_prob(payload.get("probability"), lang),
        "threshold": fmt_prob(payload.get("threshold"), lang) if payload.get("threshold") is not None else None,
        "state": state_badge(payload.get("state"), lang),
        "observed": time_html(event.valid_from, lang) if has_slot else None,
        "result_time": None if has_slot else time_html(event.issued, lang),
        "age": fmt_age(event.valid_from if has_slot else event.issued, now),
        "open_since": time_html(open_since, lang, date=False) if open_since else None,
        "flare_detected": bool(payload.get("flare_detected")),
        "location": fmt_location(location, lang),
        "on_limb": bool(isinstance(location, dict) and location.get("on_limb")),
        "bbox": bbox_percent(location),
    }


def images_context(event, lang: str) -> dict:
    """The four channels of the latest stored result (its slot; the
    service's latest for a version-1 event), each as a large image (1024 px
    WebP) and a difference (512 px: the service makes no 1024 px
    difference), with its T_OBS. 171 Å is shown first."""
    slot = _event_slot(event)
    inputs = (event.provenance or {}).get("inputs") or {} if event is not None else {}
    channels = []
    for wave in WAVES:
        t_obs = parse_time(inputs.get(wave))
        channels.append({
            "wave": wave,
            "img": image_url(wave, "img", slot, fmt="webp", size=LARGE_PX),
            "diff": image_url(wave, "diff", slot, fmt="webp"),
            "t_obs": time_html(t_obs, lang, date=False, seconds=True) if t_obs else None,
        })
    by_wave = {c["wave"]: c for c in channels}
    return {"slot": slot, "channels": channels, "default": by_wave[DEFAULT_WAVE]}


def _gradcam_slot(event: dict) -> str | None:
    """The slot of an event's peak Grad-CAM: the service's own `gradcam`
    path when it names one, else `t_pmax`."""
    path = event.get("gradcam")
    if isinstance(path, str):
        match = _GRADCAM_PATH_RE.match(path)
        if match:
            return match.group(1)
    peak = parse_time(event.get("t_pmax"))
    return iso_z(peak) if peak is not None else None


def gradcam_context(events_json, threshold: float, lang: str) -> dict:
    """"Qué miró el modelo" (D-102). Grad-CAM is coarse (a 16-px grid,
    upsampled) and normalised to its own maximum, so a quiet frame's map
    looks as bright as a flare's and means nothing: only an alert frame is
    shown. During an open event, its peak frame; otherwise the last event's
    peak ("Última alerta: …"); with no event in 30 days, nothing.

    mode: "unavailable" (service down) | "none" | "pending" (open event, no
    map yet) | "open" | "last"."""
    if events_json is None:
        return {"mode": "unavailable"}
    events = []
    for e in events_json.get("events", []):
        if isinstance(e, dict) and parse_time(e.get("t_start")) is not None:
            events.append(e)
    open_events = [e for e in events if e.get("closed") is False]
    if open_events:
        chosen, mode = max(open_events, key=lambda e: parse_time(e["t_start"])), "open"
    else:
        closed = [e for e in events if e.get("closed") is not False]
        if not closed:
            return {"mode": "none"}
        chosen = max(closed, key=lambda e: parse_time(e.get("t_pmax")) or parse_time(e["t_start"]))
        mode = "last"
    slot = _gradcam_slot(chosen)
    p_max = _number(chosen.get("p_max"))
    if slot is None or not ISO_SLOT_RE.match(slot):
        return {"mode": "pending" if mode == "open" else "none"}
    if p_max is not None and p_max < threshold:
        return {"mode": "none"}  # never a heat map for a frame below the threshold
    return {
        "mode": mode,
        "slot": slot,
        "heat": gradcam_url(slot, style="heat", fmt="webp"),
        "base": image_url("131", "img", slot, fmt="webp"),
        "when": time_html(parse_time(slot), lang),
        "probability": fmt_prob(p_max, lang),
    }


def history_rows(events_json, lang: str) -> list[dict]:
    """flare-nrt's `/api/events` -> table rows, newest first, with the GOES
    class the portal measured between t_start and t_end + 10 min, and the
    replay's data URL when the service has one (`replay: true`, 1.1.0)."""
    from django.urls import reverse

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
        event_id = e.get("event_id")
        has_replay = e.get("replay") is True and isinstance(event_id, int) and not isinstance(event_id, bool) \
            and 1 <= event_id <= 999_999_999
        rows.append({
            "event_id": event_id if has_replay else None,
            "start": time_html(start, lang),
            "peak": time_html(peak, lang, date=peak.date() != start.date()) if peak else "—",
            "end": time_html(end, lang, date=end.date() != start.date()) if end else "—",
            "open": e.get("closed") is False,
            "p_max": fmt_prob(e.get("p_max"), lang),
            "n_frames": n_frames if isinstance(n_frames, int) else "—",
            "location": fmt_location(e.get("location"), lang) or "—",
            "goes_class": goes_class or "—",
            "gradcam": public_gradcam(e.get("gradcam")),
            "replay": reverse("web:flare_replay_json", args=[event_id]) if has_replay else None,
            "replay_xray": reverse("web:flare_replay_xray_json", args=[event_id]) if has_replay else None,
        })
    return rows


# --- the frame player's data (datos/bucle.json, datos/repeticion-<id>.json) -----------

LOOP_DEFAULT_HOURS = 6
REPLAY_STATES = frozenset(STATE_LABELS)


def _unix(value) -> int | None:
    when = parse_time(value)
    return int(when.timestamp()) if when is not None else None


def loop_payload(channel: str, hours: int) -> dict:
    """The last `hours` of one pre-rendered channel, oldest first, for the
    player: {"channel", "hours", "frames": [{"t", "obs", "p", "state",
    "url"}], "unavailable"}. `t` is the slot and `obs` the 171 Å T_OBS (UNIX
    seconds, `obs` null without a result row); `url` is the public nginx
    path, never the service's (a frame whose `url` isn't the documented
    /api/render/<ISO>/<channel>.webp is dropped)."""
    data = service_json("loop", channel=channel, hours=hours)
    out = {"channel": channel, "hours": hours, "frames": [], "unavailable": data is None}
    if data is None:
        return out
    seen = set()
    for f in data.get("frames", []):
        if not isinstance(f, dict):
            continue
        url = public_render(f.get("url"), channel)
        t = _unix(f.get("slot"))
        if url is None or t is None or t in seen:
            continue
        seen.add(t)
        state = f.get("state")
        out["frames"].append({"t": t, "obs": _unix(f.get("t_obs")), "p": _number(f.get("probability")),
                              "state": state if state in REPLAY_STATES else None, "url": url})
    out["frames"].sort(key=lambda f: f["t"])
    return out


def replay_payload(event_id: int, threshold: float) -> dict:
    """One event's replay (flare-nrt `replay.json`, schema flare-nrt-replay/1)
    for the player: every frame with its 171 Å image and, at or above the
    threshold, its Grad-CAM heat map (public nginx paths); the event's
    start, peak and end; whether it is complete, with the service's note
    and missing counts when it isn't."""
    data = service_json("replay", event_id=event_id)
    out = {"event_id": event_id, "frames": [], "marks": {}, "threshold": threshold, "complete": True,
           "note": None, "missing": {"171": 0, "gradcam-heat": 0}, "n_slots": None, "window": None,
           "unavailable": data is None}
    if data is None:
        return out
    for f in data.get("frames", []):
        if not isinstance(f, dict):
            continue
        slot = f.get("slot")
        images = f.get("images") if isinstance(f.get("images"), list) else []
        t = _unix(slot)
        if t is None or not isinstance(slot, str) or not ISO_SLOT_RE.match(slot) or "171" not in images:
            continue
        p = _number(f.get("probability"))
        heat = p is not None and p >= threshold and "gradcam-heat" in images
        state = f.get("state")
        out["frames"].append({
            "t": t, "obs": _unix(f.get("t_obs")), "p": p, "state": state if state in REPLAY_STATES else None,
            "url": replay_frame_url(event_id, slot, "171"),
            "heat": replay_frame_url(event_id, slot, "gradcam-heat") if heat else None,
        })
    out["frames"].sort(key=lambda f: f["t"])
    event = data.get("event") if isinstance(data.get("event"), dict) else {}
    out["marks"] = {k: _unix(event.get(src)) for k, src in (("start", "t_start"), ("peak", "t_peak"),
                                                           ("end", "t_end"))}
    window = data.get("window") if isinstance(data.get("window"), dict) else {}
    start, end = _unix(window.get("start")), _unix(window.get("end"))
    out["window"] = [start, end] if start is not None and end is not None and start < end else None
    out["complete"] = data.get("complete") is not False
    missing = data.get("missing") if isinstance(data.get("missing"), dict) else {}
    for name in ("171", "gradcam-heat"):
        out["missing"][name] = len(missing[name]) if isinstance(missing.get(name), list) else 0
    n_slots = data.get("n_slots")
    out["n_slots"] = n_slots if isinstance(n_slots, int) and not isinstance(n_slots, bool) else None
    note = data.get("note")
    if not out["complete"] and isinstance(note, str):
        out["note"] = note[:600]
    return out


REPLAY_XRAY_MARGIN = dt.timedelta(minutes=0)
REPLAY_XRAY_MAX = dt.timedelta(hours=12)


def replay_xray(event_id: int) -> dict:
    """GOES 0.1–0.8 nm from the portal's own store over the replay's window,
    in serie.json's contract (web/series.py), for the chart under the
    player. Empty lists when the replay or its window is unknown."""
    from . import series

    data = replay_payload(event_id, DEFAULT_THRESHOLD)
    if not data["window"]:
        return series.empty_series(XRAY_PRODUCT_ID)
    start = dt.datetime.fromtimestamp(data["window"][0], dt.UTC)
    end = min(dt.datetime.fromtimestamp(data["window"][1], dt.UTC), start + REPLAY_XRAY_MAX)
    return series.window_series(XRAY_PRODUCT_ID, start, end)


# --- the home page's "El Sol ahora" (D-104) -------------------------------------------

SUN_NOW_MAX_AGE = dt.timedelta(days=3)  # the service keeps 4 days of FITS
SUN_NOW_THUMBS = ("131", "304", "1600")


def sun_now_context(lang: str, now: dt.datetime | None = None) -> dict | None:
    """The latest stored result's images for the home page, from the product
    store only (the home page never waits on the service): the 171 Å image
    at 1024 px and 512 px thumbnails of the other three, all for that
    result's slot. None (the block is not rendered) without a version-2
    result in the last 3 days."""
    now = now or timezone.now()
    event = _latest_event()
    slot = _event_slot(event)
    if slot is None or now - event.valid_from > SUN_NOW_MAX_AGE:
        return None

    def channel(wave):
        return {"wave": wave, "large": image_url(wave, "img", slot, fmt="webp", size=LARGE_PX),
                "thumb": image_url(wave, "img", slot, fmt="webp", size=512)}

    return {
        "main": channel(DEFAULT_WAVE),
        "thumbs": [channel(w) for w in SUN_NOW_THUMBS],
        "observed": time_html(event.valid_from, lang),
        "age": fmt_age(event.valid_from, now),
    }


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
            # D-102: the whole 0–1 axis, labelled at 0 and 1 too, and the
            # threshold line labelled at its left end (the newest data,
            # at the right, never covers it).
            "zero_line": False, "ref_lines": [{"value": None, "label": gettext("umbral") + " {v}",
                                               "position": "insideStartTop"}], "log_bands": [],
            "show_source": False, "kp_bucket": False, "envelope": False, "y_range": [0, 1], "anchor": "now",
            "y_interval": 0.2, "edge_labels": True,
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


def replay_xray_panels(lang: str) -> list[dict]:
    """The replay player's X-ray chart: the timeline's GOES panel on its own,
    without an endpoint (the player sets `datos/rayosx-<id>.json` per event)."""
    panel = dict(timeline_panels(lang, "", "")[1])
    panel.update(caption=None, caption_prefix=None, anchor="window", endpoint="")
    return [panel]


def page_context(lang: str) -> dict:
    now = timezone.now()
    event = _latest_event()
    # One service call: the alert history, which also decides the Grad-CAM
    # section. The header and the images come from the product store.
    events_json = service_json("events", days=30)
    return {
        "header": header_context(event, lang, now),
        "images": images_context(event, lang),
        "gradcam": gradcam_context(events_json, stored_threshold(event), lang),
        "history": history_rows(events_json, lang),
        "history_available": events_json is not None,
        "service_available": events_json is not None,
        "loop_url": None,  # filled by the view (reverse)
    }
