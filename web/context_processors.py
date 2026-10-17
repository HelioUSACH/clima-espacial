"""Template context available on every page (docs/DECISIONS.md D-033: name
and wordmark live in one setting + one template)."""

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse, translate_url
from django.utils.translation import get_language, gettext

from core.models import Event, TimeSeriesPoint

from . import charts

SITE_NAME = "Clima Espacial USACH"
SITE_BYLINE = "HelioUSACH"

# Group site lives at the host root (D-003/D-005/D-006); its own ES/EN home
# routes (helio-usach's src/lib/i18n.ts) are plain root-relative paths, so no
# FORCE_SCRIPT_NAME/reverse() involvement is needed to link to them.
GROUP_SITE_HOME = {"es": "/", "en": "/en/"}


def site(request):
    current_lang = get_language()
    other_lang = "en" if current_lang == "es" else "es"
    # D-005/12-HANDOFF item 2: `translate_url` must run on `request.path_info`
    # (the path *without* FORCE_SCRIPT_NAME), never `request.path` — resolve()
    # matches against unprefixed urlpatterns, so a script-prefixed path never
    # matches and the link silently falls back to the current page. reverse()
    # (called inside translate_url) adds the script prefix back on its own --
    # it reads a separate thread-local set for the whole request, independent
    # of the language override. The *current* language's link is just
    # request.path unchanged (already script-prefixed, no translation to do).
    other_url = translate_url(request.path_info, other_lang)
    alt_url_es = request.path if current_lang == "es" else other_url
    alt_url_en = request.path if current_lang == "en" else other_url
    return {
        "site_name": SITE_NAME,
        "site_byline": SITE_BYLINE,
        "site_contact_email": settings.SITE_CONTACT_EMAIL,
        "current_language": current_lang,
        "alt_url_es": alt_url_es,
        "alt_url_en": alt_url_en,
        # hreflang wants absolute URLs (Google's own guidance); the nav
        # lang-switch links stay relative, that's a plain in-page link.
        "alt_url_es_abs": request.build_absolute_uri(alt_url_es),
        "alt_url_en_abs": request.build_absolute_uri(alt_url_en),
        "group_site_home": GROUP_SITE_HOME[current_lang],
    }


# Status strip (18-HANDOFF-v0.2.0.md §2): on every page, so it's a context
# processor, not a per-view render() call. The raw numbers are cached for
# everyone (language-independent); each request only localises labels.
_STRIP_CACHE_KEY = "swx:strip:v1"
_STRIP_CACHE_SECONDS = 30

_SCALE_TITLES = {
    "R": {"es": "Radio", "en": "Radio"},
    "S": {"es": "Radiación", "en": "Radiation"},
    "G": {"es": "Geomagnética", "en": "Geomagnetic"},
}


def _strip_raw():
    cached = cache.get(_STRIP_CACHE_KEY)
    if cached is not None:
        return cached
    scales_event = Event.objects.filter(product_id="noaa.scales").order_by("-issued").first()
    today = (scales_event.payload.get("days", {}).get("0", {}) if scales_event else {}) or {}
    scale_levels = {}
    for letter in ("R", "S", "G"):
        level_str = (today.get(letter) or {}).get("Scale")
        scale_levels[letter] = int(level_str) if level_str not in (None, "") else 0

    kp_row = TimeSeriesPoint.objects.filter(product_id="noaa.kp").order_by("-time_tag").first()
    dst_row = TimeSeriesPoint.objects.filter(product_id="noaa.dst").order_by("-time_tag").first()
    mag_row = TimeSeriesPoint.objects.filter(product_id="noaa.rtsw.mag").order_by("-time_tag").first()

    raw = {
        "scale_levels": scale_levels,
        "kp": kp_row.payload.get("kp") if kp_row else None,
        "dst": dst_row.payload.get("dst") if dst_row else None,
        "source": mag_row.source if mag_row else None,
        "last_seen_at": mag_row.time_tag if mag_row else None,
    }
    cache.set(_STRIP_CACHE_KEY, raw, _STRIP_CACHE_SECONDS)
    return raw


def strip(request):
    # Imported here, not at module level: web.views doesn't import this
    # module, so there's no real cycle, but keeping it local makes that
    # non-cycle obvious on read.
    from .views import _fmt_ut

    lang = "en" if get_language() == "en" else "es"
    raw = _strip_raw()
    scales = [
        {
            "letter": letter,
            "title": _SCALE_TITLES[letter][lang],
            "level": level,
            "severity": charts.SCALE_SEVERITY.get(level, "ok"),
            "url": reverse("web:product_detail", args=["noaa.scales"]),
        }
        for letter, level in raw["scale_levels"].items()
    ]
    any_alert = any(s["level"] > 0 for s in scales)
    # Pre-formatted here, not with the template's `floatformat` (locale-aware
    # -- a comma decimal on an es page would mismatch the charts' own
    # plain-period JS formatting; found and fixed the same way in D-053).
    kp_chip = {
        "value": f"{raw['kp']:.1f}" if raw["kp"] is not None else None,
        "severity": charts.kp_severity(raw["kp"]) if raw["kp"] is not None else None,
        "url": reverse("web:product_detail", args=["noaa.kp"]),
    }
    dst_chip = {
        "value": f"{raw['dst']:.0f}" if raw["dst"] is not None else None,
        "severity": charts.dst_severity(raw["dst"]) if raw["dst"] is not None else None,
        "url": reverse("web:product_detail", args=["noaa.dst"]),
    }
    return {
        "strip_scales": scales,
        "strip_any_alert": any_alert,
        "strip_kp": kp_chip,
        "strip_dst": dst_chip,
        "strip_source": raw["source"],
        "strip_last_seen_at": _fmt_ut(raw["last_seen_at"], lang) if raw["last_seen_at"] else None,
        "strip_mag_url": reverse("web:product_detail", args=["noaa.rtsw.mag"]),
        "strip_no_alert_text": gettext("sin alertas NOAA"),
    }
