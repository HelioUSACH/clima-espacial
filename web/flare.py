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

Image geometry (location overlay, v0.6.3, D-117): flare-nrt's display
images are 512 px, north up. Where helioprojective arcsec fall on them is
flare-nrt 1.3.0's `geometry` (replay.json: per frame, the peak's, the
replay's), from each frame's 131 Å header; it is never assumed here. A live
location (/api/status has no geometry) is drawn with the newest replay's
geometry and the location's own scale (location_geometry()). A location
is also given in words, Stonyhurst latitude and longitude ("S12 W68",
heliographic(), D-116).
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


def fmt_location(location, lang: str, *, parens: bool = True) -> str:
    """'(−412″, 269″)', helioprojective x (west +) and y (north +); without
    the parentheses in a table cell (D-108)."""
    if not isinstance(location, dict):
        return ""
    centroid = location.get("centroid_arcsec")
    if not (isinstance(centroid, list) and len(centroid) == 2
            and all(isinstance(v, (int, float)) for v in centroid)):
        return ""
    text = f"{_arcsec(centroid[0], lang)}, {_arcsec(centroid[1], lang)}"
    return f"({text})" if parens else text


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


# --- geometry and location (v0.6.3, D-116..D-118) -----------------------------------

IMAGE_PX = 512  # flare-nrt's display images (512 px, north up); not a disk size
# The location's two methods (flare-nrt 1.3.0 INTEGRATION.md "Location"): the
# first rule's, and the robust fallback found after the 131 Å maximum (not
# validated; often post-flare loops next to the kernel). D-118.
ROBUST_SUFFIX = "_robust"


def _pair(value) -> tuple[float, float] | None:
    if not (isinstance(value, (list, tuple)) and len(value) == 2):
        return None
    a, b = (_number(v) for v in value)
    return (a, b) if a is not None and b is not None else None


def parse_geometry(geometry) -> dict | None:
    """flare-nrt 1.3.0's `geometry` (replay.json: per frame, the peak's, the
    replay's): where helioprojective arcsec fall on the 512-px north-up
    images. {size, cx, cy, ax, ay, r} or None when anything is missing or
    nonsensical. Pixel coordinates from the top-left corner, x right
    (west), y down; a point (x″, y″) is at col = cx + x/ax, row = cy − y/ay."""
    if not isinstance(geometry, dict):
        return None
    size = _number(geometry.get("size_px"))
    centre, scale = _pair(geometry.get("disk_center_px")), _pair(geometry.get("arcsec_per_px"))
    radius = _number(geometry.get("disk_radius_px"))
    if not size or size <= 0 or centre is None or scale is None or scale[0] <= 0 or scale[1] <= 0:
        return None
    return {"size": size, "cx": centre[0], "cy": centre[1], "ax": scale[0], "ay": scale[1],
            "r": radius if radius and radius > 0 else None}


def location_geometry(location, reference=None) -> dict | None:
    """The geometry to draw a *live* location with (/api/status has none,
    D-117): the newest replay's (`reference`, parse_geometry; the synoptic
    disk centre is fixed and the radius changes by < 0.1 % over days), its
    scale replaced by the location's own when it carries one
    (area_arcsec2 / area_px is that frame's arcsec² per pixel on the 512
    grid). Without a reference, the location's own scale about the image
    centre. None when neither gives a scale: nothing is drawn rather than
    guess one."""
    own = None
    if isinstance(location, dict):
        area_px, area_as = _number(location.get("area_px")), _number(location.get("area_arcsec2"))
        if area_px and area_as and area_px > 0 and area_as > 0:
            own = math.sqrt(area_as / area_px)
    if reference is not None:
        return dict(reference, ax=own, ay=own) if own else dict(reference)
    if own:
        half = IMAGE_PX / 2
        return {"size": float(IMAGE_PX), "cx": half, "cy": half, "ax": own, "ay": own, "r": None}
    return None


def is_approx(location) -> bool:
    """A location from flare-nrt 1.3.0's robust fallback (D-118)."""
    method = location.get("method") if isinstance(location, dict) else None
    return isinstance(method, str) and method.endswith(ROBUST_SUFFIX)


def loc_shape(location, geometry) -> dict | None:
    """An experimental location -> what the page draws over a 512-px image:
    {left, top, width, height} the bbox and {cx, cy} the centroid, as CSS
    percentages of the image (strings with a decimal point: a template must
    not localise them), clipped to the image; `approx` for a robust
    location (dashed). `geometry` is parse_geometry()'s, never assumed.
    None without a usable box or centroid, or off the image."""
    if not isinstance(location, dict) or geometry is None:
        return None
    size, cx, cy, ax, ay = (geometry[k] for k in ("size", "cx", "cy", "ax", "ay"))

    def col(x):
        return cx + x / ax

    def row(y):
        return cy - y / ay  # north up: +y is towards the top

    def pct(v):
        return f"{100 * v / size:.2f}"

    box = location.get("bbox_arcsec")
    nums = [_number(v) for v in box] if isinstance(box, list) and len(box) == 4 else None
    centroid = _pair(location.get("centroid_arcsec"))
    out = {"approx": is_approx(location)}
    if nums and all(v is not None for v in nums):
        x0, y0, x1, y1 = nums
        left, right = sorted((col(x0), col(x1)))
        top, bottom = sorted((row(y1), row(y0)))
        left, top = max(left, 0.0), max(top, 0.0)
        right, bottom = min(right, size), min(bottom, size)
        if right > left and bottom > top:
            out.update(left=pct(left), top=pct(top), width=pct(right - left), height=pct(bottom - top))
            if centroid is None:
                out.update(cx=pct((left + right) / 2), cy=pct((top + bottom) / 2))
    if centroid is not None:
        px, py = col(centroid[0]), row(centroid[1])
        if 0 <= px <= size and 0 <= py <= size:
            out.update(cx=pct(px), cy=pct(py))
            if "left" not in out:  # a centroid alone: a zero box, drawn as the circle
                out.update(left=out["cx"], top=out["cy"], width="0.00", height="0.00")
    return out if "cx" in out else None


# --- heliographic coordinates of a location ("S12 W68", D-116) -------------------------

def _sun_b0_and_radius(when: dt.datetime) -> tuple[float, float]:
    """(B0 in degrees, the Sun's apparent radius in arcsec) seen from Earth
    at `when`: low-precision solar coordinates (Meeus, Astronomical
    Algorithms ch. 25 and 29), good to ~0.01° in B0, far below the
    location's own uncertainty. SDO's geosynchronous orbit is within
    0.0003 AU of Earth."""
    jd = when.timestamp() / 86400 + 2440587.5
    t = (jd - 2451545.0) / 36525
    l0 = 280.46646 + 36000.76983 * t
    m = math.radians(357.52911 + 35999.05029 * t)
    e = 0.016708634 - 0.000042037 * t
    c = ((1.914602 - 0.004817 * t) * math.sin(m) + (0.019993 - 0.000101 * t) * math.sin(2 * m)
         + 0.000289 * math.sin(3 * m))
    true_lon = l0 + c
    nu = m + math.radians(c)
    distance_au = 1.000001018 * (1 - e * e) / (1 + e * math.cos(nu))
    k = 73.6667 + 1.3958333 * (jd - 2396758.0) / 36525
    incl = math.radians(7.25)
    b0 = math.degrees(math.asin(math.sin(math.radians(true_lon - k)) * math.sin(incl)))
    radius = math.degrees(math.asin(696_000 / (distance_au * 149_597_870.7))) * 3600
    return b0, radius


def heliographic(location, when: dt.datetime | None, rsun_arcsec: float | None = None) -> tuple[float, float] | None:
    """A location's centroid (helioprojective arcsec) -> Stonyhurst
    heliographic (latitude, longitude) in degrees, north and west positive,
    at `when` (B0 from the date). The line of sight is intersected with the
    solar sphere; a centroid beyond the limb (flare-nrt's on-limb case) is
    put on the limb. `rsun_arcsec`: the frame's own (geometry), else the
    date's. None without a centroid or a time."""
    centroid = _pair(location.get("centroid_arcsec")) if isinstance(location, dict) else None
    if centroid is None or when is None:
        return None
    b0_deg, radius = _sun_b0_and_radius(when)
    if rsun_arcsec and rsun_arcsec > 0:
        radius = rsun_arcsec
    rs = math.radians(radius / 3600)
    d = 1 / math.sin(rs)  # observer distance, in solar radii
    tx, ty = (math.radians(v / 3600) for v in centroid)
    u = (math.cos(ty) * math.sin(tx), math.sin(ty), -math.cos(ty) * math.cos(tx))
    ou = d * u[2]  # observer at (0, 0, d)
    disc = ou * ou - (d * d - 1)
    s = -ou - math.sqrt(disc) if disc > 0 else -ou
    x, y, z = s * u[0], s * u[1], d + s * u[2]
    norm = math.sqrt(x * x + y * y + z * z) or 1.0
    x, y, z = x / norm, y / norm, z / norm
    b0 = math.radians(b0_deg)
    lat = math.degrees(math.asin(max(-1.0, min(1.0, y * math.cos(b0) + z * math.sin(b0)))))
    lon = math.degrees(math.atan2(x, z * math.cos(b0) - y * math.sin(b0)))
    return lat, lon


def fmt_heliographic(location, when: dt.datetime | None, rsun_arcsec: float | None = None) -> str:
    """'S12 W68': latitude and longitude, whole degrees, NOAA's order and
    letters (D-116); '' without a centroid."""
    hg = heliographic(location, when, rsun_arcsec)
    if hg is None:
        return ""
    lat, lon = (int(round(v)) for v in hg)
    return f"{'S' if lat < 0 else 'N'}{abs(lat):02d} {'E' if lon < 0 else 'W'}{abs(lon):02d}"


def location_view(location, when: dt.datetime | None, lang: str, *, geometry=None, shape_geometry=None) -> dict | None:
    """What the page shows of a location: `text` in words ("S12 W68"),
    `arcsec` (the centroid, "−412″, 269″"), `approx` (robust method,
    D-118) and `shape` (loc_shape over `shape_geometry`, None when there is
    no geometry to draw it with). None without a centroid."""
    if not isinstance(location, dict):
        return None
    rsun = None
    if isinstance(geometry, dict):
        rsun = _number(geometry.get("rsun_arcsec"))
    text = fmt_heliographic(location, when, rsun)
    if not text:
        return None
    return {"text": text, "arcsec": fmt_location(location, lang, parens=False), "approx": is_approx(location),
            "shape": loc_shape(location, shape_geometry)}


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
    /api/events/<id>/frames/<ISO>/<name>.webp, through nginx; name 171,
    gradcam-heat, or 131 since flare-nrt 1.2.0)."""
    return f"{media_base()}/replay/{int(event_id)}/{slot}/{name}.webp"


# An event's peak set (flare-nrt 1.2.0): the peak slot's four channels and
# its Grad-CAM, kept with the replay 365 days. Only these names are asked
# for, and only those replay.json's `peak.images` lists (D-112).
PEAK_NAMES = ("131", "171", "304", "1600", "gradcam-heat", "gradcam-overlay")


def peak_url(event_id: int, name: str) -> str:
    """One file of an event's peak set, public path (the service's
    /api/events/<id>/peak/<name>.webp, through nginx)."""
    if name not in PEAK_NAMES:
        raise ValueError(f"not a peak file: {name!r}")
    return f"{media_base()}/replay/{int(event_id)}/peak/{name}.webp"


# --- the page ---------------------------------------------------------------------

WAVES = ("131", "171", "304", "1600")
LOC_WAVES = ("131", "171")
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


def header_context(event, lang: str, now: dt.datetime, reference_geometry=None) -> dict:
    """The header, from the latest stored event. Works for a version-1 event
    (no threshold, state, observation slot or provenance): those parts are
    simply left out. `loc`: the open event's location (location_view), its
    box drawn with location_geometry(…, reference_geometry) (D-117)."""
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
        "loc": location_view(location, event.valid_from, lang,
                             shape_geometry=location_geometry(location, reference_geometry)) if location else None,
        "on_limb": bool(isinstance(location, dict) and location.get("on_limb")),
    }


def images_context(event, lang: str) -> dict:
    """The four channels of the latest stored result (its slot; the
    service's latest for a version-1 event), for the grid of eight tiles
    (D-106): each channel's image and difference at 512 px WebP, with its
    T_OBS; the lightbox opens the image at 1024 px (the service makes no
    1024 px difference, so a difference opens at 512)."""
    slot = _event_slot(event)
    inputs = (event.provenance or {}).get("inputs") or {} if event is not None else {}
    channels = []
    for wave in WAVES:
        t_obs = parse_time(inputs.get(wave))
        channels.append({
            "wave": wave,
            "img": image_url(wave, "img", slot, fmt="webp"),
            "img_large": image_url(wave, "img", slot, fmt="webp", size=LARGE_PX),
            "diff": image_url(wave, "diff", slot, fmt="webp"),
            "t_obs": time_html(t_obs, lang, date=False, seconds=True) if t_obs else None,
            # The location is drawn on 131 Å (where it was found) and 171 Å (D-117).
            "loc": wave in LOC_WAVES,
        })
    return {"slot": slot, "channels": channels}


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


def _event_id(e: dict) -> int | None:
    value = e.get("event_id")
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 999_999_999:
        return value
    return None


# flare-nrt keeps the FITS (and so /api/image for a past slot) 4 days; an
# event replay keeps its own 131 Å frames 365 days (1.2.0).
LIVE_IMAGE_DAYS = dt.timedelta(days=4)


def cam_sources(event_id: int | None, slot: str, *, has_replay: bool, now: dt.datetime,
                peak_images=None, frame_images=None) -> dict:
    """Where the panel's Grad-CAM heat map and the 131 Å image under it
    come from, in order (D-112); flare.js tries the next on an error, so
    the page never shows a broken image.

    heat: the peak set's gradcam-heat (1.2.0); the replay's heat frame at
    that slot; the live gradcam/<slot>?style=heat; then the overlay (the
    peak set's, then the live one), which already carries its 131 Å image
    and is labelled as such. 131 Å: the peak set's 131; the live image
    while flare-nrt has that slot's FITS (4 days); the replay's 131 Å
    frame; then nothing (the map alone, it carries the limb).

    `peak_images` and `frame_images` are replay.json's `peak.images` and
    the `images` of its frame at `slot`; None when replay.json wasn't read
    (then the peak set is not asked for, and the replay's 131 Å frame is,
    as in v0.6.2's first draft)."""
    peak = [n for n in PEAK_NAMES if n in (peak_images or ())] if has_replay else []
    known = frame_images is not None
    frame = frame_images or ()
    heat = []
    if "gradcam-heat" in peak:
        heat.append({"src": peak_url(event_id, "gradcam-heat"), "overlay": False})
    if has_replay and known and "gradcam-heat" in frame:
        heat.append({"src": replay_frame_url(event_id, slot, "gradcam-heat"), "overlay": False})
    heat.append({"src": gradcam_url(slot, style="heat", fmt="webp"), "overlay": False})
    if "gradcam-overlay" in peak:
        heat.append({"src": peak_url(event_id, "gradcam-overlay"), "overlay": True})
    heat.append({"src": gradcam_url(slot, style="overlay", fmt="webp"), "overlay": True})
    base = []
    if "131" in peak:
        base.append(peak_url(event_id, "131"))
    if now - parse_time(slot) < LIVE_IMAGE_DAYS:
        base.append(image_url("131", "img", slot, fmt="webp"))
    if has_replay and (not known or "131" in frame):
        base.append(replay_frame_url(event_id, slot, "131"))
    return {"slot": slot, "heat": heat, "base": base}


def apply_cam(view: dict, cam: dict, lang: str, *, ready: bool) -> None:
    """Put cam_sources()'s lists into an event's view (the panel template
    reads cam, cam_heat_json, cam_base_json, cam_when)."""
    view["cam"] = cam
    view["cam_slot"] = cam["slot"]
    view["cam_when"] = time_html(parse_time(cam["slot"]), lang)
    view["cam_heat_json"] = json.dumps(cam["heat"], separators=(",", ":"))
    view["cam_base_json"] = json.dumps(cam["base"], separators=(",", ":"))
    view["cam_ready"] = ready


def apply_location(view: dict, loc: dict | None, *, shape: bool = False) -> None:
    """An event's location into its view: the table cell and the panel read
    `location` (words, "—" without one) and `loc`; the panel's image draws
    `loc_shape` (only from replay.json, which has the geometry)."""
    if loc is None:
        return
    view["loc"] = loc
    view["location"] = loc["text"]
    if shape:
        view["loc_shape"] = loc.get("shape")


def _cell_time(when: dt.datetime | None, lang: str, *, date: bool = True) -> str:
    """A table cell's time: UT on one line, local time on a second (D-108)."""
    return time_html(when, lang, date=date, stacked=True) if when is not None else "—"


def _event_view(e: dict, start, peak, end, goes_class, threshold: float, lang: str, now: dt.datetime) -> dict:
    """One event as the table row and the panel show it (D-107)."""
    from django.urls import reverse

    event_id = _event_id(e)
    has_replay = e.get("replay") is True and event_id is not None
    n_frames = e.get("n_alert_frames")
    p_max = _number(e.get("p_max"))
    is_open = e.get("closed") is False
    slot = _gradcam_slot(e)
    if slot is not None and not ISO_SLOT_RE.match(slot):
        slot = None
    # "Qué miró el modelo" (D-102): never a map for a frame below the
    # threshold; an open event may not have one yet.
    if slot is None:
        cam_mode = "pending" if is_open else "none"
    elif p_max is not None and p_max < threshold:
        cam_mode = "none"
    else:
        cam_mode = "map"
    same_day_peak = peak is not None and peak.date() == start.date()
    same_day_end = end is not None and end.date() == start.date()
    view = {
        "event_id": event_id,
        "open": is_open,
        "start": _cell_time(start, lang),
        "peak": _cell_time(peak, lang, date=not same_day_peak) if peak else "—",
        "end": _cell_time(end, lang, date=not same_day_end) if end else "—",
        "start_full": time_html(start, lang),
        "peak_full": time_html(peak, lang) if peak else "—",
        "end_full": time_html(end, lang) if end else "—",
        "p_max": fmt_prob(p_max, lang),
        "n_frames": n_frames if isinstance(n_frames, int) and not isinstance(n_frames, bool) else "—",
        "location": "—",
        "loc": None,
        "loc_shape": None,
        "goes_class": goes_class or "—",
        "cam_mode": cam_mode,
        "cam": None,
        "cam_slot": None,
        "cam_when": None,
        "cam_ready": False,
        "replay": reverse("web:flare_replay_json", args=[event_id]) if has_replay else None,
        "replay_xray": reverse("web:flare_replay_xray_json", args=[event_id]) if has_replay else None,
    }
    apply_location(view, location_view(e.get("location"), peak or start, lang))
    if cam_mode == "map":
        # Without replay.json (read for the selected event only, page_context;
        # flare.js reads it for the others before showing them).
        apply_cam(view, cam_sources(event_id, slot, has_replay=has_replay, now=now), lang, ready=not has_replay)
    return view


def history_rows(events_json, lang: str, threshold: float = DEFAULT_THRESHOLD,
                 now: dt.datetime | None = None) -> list[dict]:
    """flare-nrt's `/api/events` -> one view per event, newest first, with
    the GOES class the portal measured between t_start and t_end + 10 min,
    the panel's Grad-CAM and 131 Å URLs and the replay's data URLs when the
    service has one (`replay: true`, 1.1.0)."""
    now = now or timezone.now()
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
    return [_event_view(e, start, peak, end, goes_class, threshold, lang, now)
            for (e, start, peak, end), goes_class in zip(parsed, classes, strict=True)]


_EVENT_PARAM_RE = re.compile(r"[0-9]{1,9}")


def parse_event_param(value) -> int | None:
    """`?evento=<id>`: ASCII digits only, 1..999 999 999; anything else is
    None (str.isdigit() would also take "４" or "²")."""
    if not isinstance(value, str) or not _EVENT_PARAM_RE.fullmatch(value):
        return None
    number = int(value)
    return number if number >= 1 else None


def select_event(rows: list[dict], wanted: int | None) -> dict | None:
    """The event the panel shows (D-107): the one asked for in the URL if it
    is in the list; else the open event; else the most recent one."""
    selectable = [r for r in rows if r["event_id"] is not None]
    if wanted is not None:
        for r in selectable:
            if r["event_id"] == wanted:
                return r
    for r in selectable:
        if r["open"]:
            return r
    return selectable[0] if selectable else None


# --- the frame player's data (datos/bucle.json, datos/repeticion-<id>.json) -----------

LOOP_DEFAULT_HOURS = 6
REPLAY_STATES = frozenset(STATE_LABELS)
# D-115: a replay frame's Grad-CAM map is drawn with opacity
# slider × w(p), w = clamp((p − CAM_FADE_FLOOR) / (threshold − CAM_FADE_FLOOR), 0, 1):
# transparent at or below this probability, full at the threshold. Each map
# is normalised to its own maximum, so a quiet frame's would otherwise show
# bright blobs that mean nothing. flare-player.js reads it from the replay's
# `cam_floor`; the note under the player prints it.
CAM_FADE_FLOOR = 0.30


def _unix(value) -> int | None:
    when = parse_time(value)
    return int(when.timestamp()) if when is not None else None


def reference_geometry(events_json=None) -> dict | None:
    """The newest replay's geometry (parse_geometry), for drawing a live
    location (/api/status has none, D-117): from the newest event in
    /api/events (30 days) whose replay has one. None without one."""
    events_json = events_json if events_json is not None else service_json("events", days=30)
    events = [e for e in (events_json or {}).get("events", []) if isinstance(e, dict)]
    events.sort(key=lambda e: _unix(e.get("t_start")) or 0, reverse=True)
    for e in events:
        event_id = _event_id(e)
        if e.get("replay") is True and event_id is not None:
            data = service_json("replay", event_id=event_id)
            geometry = parse_geometry((data or {}).get("geometry"))
            if geometry is not None:
                return geometry
            break  # only the newest: an older one is not worth another call
    return None


def live_location() -> dict | None:
    """The open event's location from /api/status, for the 6-h loop's
    newest frames: {"shape", "since"} (UNIX seconds of event_t_start;
    frames from then on carry it). None while no event is open, or when it
    can't be drawn (no geometry, D-117)."""
    status = service_json("status")
    if not status or status.get("flare_detected") is not True:
        return None
    location = status.get("location")
    since = _unix(status.get("event_t_start")) or _unix(status.get("observation_time"))
    if not isinstance(location, dict) or since is None:
        return None
    shape = loc_shape(location, location_geometry(location, reference_geometry()))
    return {"shape": shape, "since": since} if shape else None


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
    live = live_location()
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
                              "state": state if state in REPLAY_STATES else None, "url": url,
                              "loc": live["shape"] if live and t >= live["since"] else None})
    out["frames"].sort(key=lambda f: f["t"])
    return out


def replay_payload(event_id: int, threshold: float, now: dt.datetime | None = None) -> dict:
    """One event's replay (flare-nrt `replay.json`, schema flare-nrt-replay/1)
    for the player: every frame with its 171 Å image and its Grad-CAM heat
    map (public nginx paths): since flare-nrt 1.3.0 every frame with a
    result has one, which the player weights by probability (`cam_floor`,
    D-115); an older replay's maps (alert frames only) are used at or above
    the threshold, as before. Per frame also `alert` and `loc` (the event's
    location over that frame's geometry, from t_start to t_end, D-117). The
    event's start, peak and end; whether it is complete, with the service's
    note and missing counts when it isn't; its peak set (1.2.0, `peak`: slot
    and the public URL of each file replay.json lists), the event panel's
    image sources (`cam`, cam_sources()) and the event's `location`
    (location_view, its shape over the peak frame's geometry)."""
    data = service_json("replay", event_id=event_id)
    out = {"event_id": event_id, "frames": [], "marks": {}, "threshold": threshold, "cam_floor": CAM_FADE_FLOOR,
           "complete": True, "note": None, "missing": {"171": 0, "gradcam-heat": 0}, "n_slots": None,
           "window": None, "peak": None, "cam": None, "location": None, "geometry": None,
           "unavailable": data is None}
    if data is None:
        return out
    own = _number(data.get("threshold"))
    if own is not None and 0 < own <= 1:
        threshold = out["threshold"] = own
    raw_frames = [f for f in data.get("frames", []) if isinstance(f, dict)]
    # 1.3.0 marks itself: `threshold` and the frames' `cam_peak`. Before it,
    # only alert frames had a map.
    every_map = "threshold" in data or any("cam_peak" in f for f in raw_frames)
    event = data.get("event") if isinstance(data.get("event"), dict) else {}
    peak = data.get("peak") if isinstance(data.get("peak"), dict) else None
    geometry = parse_geometry(data.get("geometry"))
    out["geometry"] = geometry
    event_loc = event.get("location") if isinstance(event.get("location"), dict) else None
    if event_loc is None and peak and isinstance(peak.get("location"), dict):
        event_loc = peak["location"]
    t_start, t_end = _unix(event.get("t_start")), _unix(event.get("t_end"))
    if t_start is not None and t_end is None:
        t_end = t_start
    images_at = {}
    for f in raw_frames:
        slot = f.get("slot")
        images = f.get("images") if isinstance(f.get("images"), list) else []
        t = _unix(slot)
        if t is not None and isinstance(slot, str) and ISO_SLOT_RE.match(slot):
            images_at[t] = [n for n in images if isinstance(n, str)]
        if t is None or not isinstance(slot, str) or not ISO_SLOT_RE.match(slot) or "171" not in images:
            continue
        p = _number(f.get("probability"))
        heat = p is not None and "gradcam-heat" in images and (every_map or p >= threshold)
        state = f.get("state")
        alert = f.get("alert") if isinstance(f.get("alert"), bool) else (p is not None and p >= threshold)
        in_event = t_start is not None and t_start <= t <= t_end
        frame_geometry = parse_geometry(f.get("geometry")) or geometry
        out["frames"].append({
            "t": t, "obs": _unix(f.get("t_obs")), "p": p, "state": state if state in REPLAY_STATES else None,
            "alert": bool(alert and p is not None),
            "url": replay_frame_url(event_id, slot, "171"),
            "heat": replay_frame_url(event_id, slot, "gradcam-heat") if heat else None,
            "loc": loc_shape(event_loc, frame_geometry) if in_event else None,
        })
    out["frames"].sort(key=lambda f: f["t"])
    out["marks"] = {k: _unix(event.get(src)) for k, src in (("start", "t_start"), ("peak", "t_peak"),
                                                           ("end", "t_end"))}
    peak_slot = peak.get("slot") if peak else None
    if isinstance(peak_slot, str) and ISO_SLOT_RE.match(peak_slot):
        names = [n for n in PEAK_NAMES if n in (peak.get("images") if isinstance(peak.get("images"), list) else ())]
        out["peak"] = {"slot": peak_slot, "t": _unix(peak_slot), "probability": _number(peak.get("probability")),
                       "gradcam_peak": _number(peak.get("gradcam_peak")),
                       "images": {n: peak_url(event_id, n) for n in names}}
        cam_slot = peak_slot
    else:  # a replay built by 1.1.0: no peak set
        names = []
        t_peak = parse_time(event.get("t_peak"))
        cam_slot = iso_z(t_peak) if t_peak is not None else None
    peak_geometry = (parse_geometry(peak.get("geometry")) if peak else None) or geometry
    when = parse_time(event.get("t_peak")) or parse_time(event.get("t_start"))
    loc = location_view(event_loc, when, "es", geometry=(peak or {}).get("geometry") or data.get("geometry"),
                        shape_geometry=peak_geometry)
    if loc is not None:
        out["location"] = {"text": loc["text"], "approx": loc["approx"], "shape": loc["shape"]}
    if cam_slot is not None:
        out["cam"] = cam_sources(event_id, cam_slot, has_replay=True, now=now or timezone.now(),
                                 peak_images=names, frame_images=images_at.get(_unix(cam_slot), []))
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
SUN_NOW_WAVES = WAVES  # fixed order, never reordered (D-106)


def sun_now_context(lang: str, now: dt.datetime | None = None) -> dict | None:
    """The latest stored result's images for the home page, from the product
    store only (the home page never waits on the service): four 512 px
    thumbnails in a fixed order (131, 171, 304, 1600) and the selected one
    at 1024 px, 171 Å by default, all for that result's slot. None (the
    block is not rendered) without a version-2 result in the last 3 days."""
    now = now or timezone.now()
    event = _latest_event()
    slot = _event_slot(event)
    if slot is None or now - event.valid_from > SUN_NOW_MAX_AGE:
        return None

    def channel(wave):
        return {"wave": wave, "large": image_url(wave, "img", slot, fmt="webp", size=LARGE_PX),
                "thumb": image_url(wave, "img", slot, fmt="webp", size=512), "selected": wave == DEFAULT_WAVE}

    thumbs = [channel(w) for w in SUN_NOW_WAVES]
    return {
        "main": next(t for t in thumbs if t["selected"]),
        "thumbs": thumbs,
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


def page_context(lang: str, wanted_event: int | None = None) -> dict:
    now = timezone.now()
    event = _latest_event()
    # One service call: the alert history, which is also the "Eventos"
    # selector (D-107). The header and the images come from the product store.
    events_json = service_json("events", days=30)
    threshold = stored_threshold(event)
    rows = history_rows(events_json, lang, threshold, now)
    selected = select_event(rows, wanted_event)
    if selected is not None and selected["replay"]:
        # The selected event's peak set (1.2.0) and its location's geometry
        # (1.3.0) need its replay.json: one more call, cached like the
        # player's (the same key).
        replay = replay_payload(selected["event_id"], threshold, now=now)
        if replay["cam"] is not None and selected["cam_mode"] == "map":
            apply_cam(selected, replay["cam"], lang, ready=True)
        if replay["location"] is not None:
            apply_location(selected, dict(selected["loc"] or {}, **replay["location"]), shape=True)
    header_event = (event.payload or {}) if event is not None else {}
    reference = reference_geometry(events_json) if header_event.get("flare_detected") else None
    return {
        "header": header_context(event, lang, now, reference),
        "images": images_context(event, lang),
        "history": rows,
        "selected": selected,
        "history_available": events_json is not None,
        "service_available": events_json is not None,
        "loop_url": None,  # filled by the view (reverse)
    }
