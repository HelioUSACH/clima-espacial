"""Template context available on every page (docs/DECISIONS.md D-033: name
and wordmark live in one setting + one template)."""

from django.conf import settings

SITE_NAME = "Clima Espacial USACH"
SITE_BYLINE = "HelioUSACH"


def site(request):
    return {
        "site_name": SITE_NAME,
        "site_byline": SITE_BYLINE,
        "goatcounter_site": settings.GOATCOUNTER_SITE,
    }
