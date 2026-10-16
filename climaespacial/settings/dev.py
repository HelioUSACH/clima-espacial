from .base import *  # noqa: F401,F403
from .base import ALLOWED_HOSTS, SECRET_KEY, SWX_AMBIENTE, ImproperlyConfigured

if SWX_AMBIENTE != "dev":
    raise ImproperlyConfigured("settings.dev runs with DEBUG on: use settings.prod for staging/produccion.")

DEBUG = True
SECRET_KEY = SECRET_KEY or "django-insecure-solo-para-desarrollo-local"
ALLOWED_HOSTS = ALLOWED_HOSTS or ["localhost", "127.0.0.1"]

# Plain storage in dev and under pytest (which runs these settings): no
# manifest to build first. Staging/production keep base.py's hashed
# ManifestStaticFilesStorage (D-055).
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
