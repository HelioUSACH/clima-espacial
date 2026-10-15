"""Template context available on every page (docs/DECISIONS.md D-033: name
and wordmark live in one setting + one template)."""

from django.conf import settings
from django.urls import translate_url
from django.utils.translation import get_language

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
