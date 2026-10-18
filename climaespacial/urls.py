from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.urls import include, path

from api.urls import api_patterns, hapi_patterns

urlpatterns = [
    path("i18n/", include("django.conf.urls.i18n")),
    path("gestion/", admin.site.urls),
    # Language-neutral data access (D-086..D-089): no /en/ variant.
    path("", include(hapi_patterns)),
    path("api/v1/", include(api_patterns)),
]

urlpatterns += i18n_patterns(
    path("", include("web.urls")),
    prefix_default_language=False,  # ES has no prefix; EN is under /en/ (D-005)
)

if settings.DEBUG:
    from django.conf.urls.static import static

    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
