"""Settings shared by every environment. Values come from environment
variables (.env locally, /etc/swx/swx.env on the server): see .env.example."""

import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")


def env_list(name, default=""):
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


def database_from_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ("postgres", "postgresql"):
        raise ImproperlyConfigured("DATABASE_URL must be a postgresql:// URL (see .env.example).")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parts.path.lstrip("/")),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        # The server connects through the local socket: ...?host=/var/run/postgresql
        "HOST": parts.hostname or parse_qs(parts.query).get("host", [""])[0],
        "PORT": str(parts.port or ""),
    }


# --- Platform settings --------------------------------------------------------

AMBIENTES = ("dev", "staging", "produccion")

SWX_AMBIENTE = os.environ.get("SWX_AMBIENTE", "dev")
if SWX_AMBIENTE not in AMBIENTES:
    raise ImproperlyConfigured(f"SWX_AMBIENTE must be one of {', '.join(AMBIENTES)}.")

SWX_HTTPS = os.environ.get("SWX_HTTPS", "0") == "1"

# Sub-path deployment (D-005): FORCE_SCRIPT_NAME + STATIC_URL share one prefix,
# set once here instead of scattered across templates/urls.
SWX_SCRIPT_NAME = os.environ.get("SWX_SCRIPT_NAME", "").rstrip("/")
if SWX_SCRIPT_NAME:
    FORCE_SCRIPT_NAME = SWX_SCRIPT_NAME

SWX_DATA_DIR = Path(os.environ.get("SWX_DATA_DIR") or BASE_DIR / "data")

# D-051/P6: shown on the data/API registration form's privacy notice. No
# real address is known yet (not something to guess at for a form that
# collects personal data) — set SWX_CONTACT_EMAIL before taking real
# registrations. Empty (not an internal placeholder string, 19-HANDOFF-
# v0.2.1.md §1.5: the old "[pendiente — ver docs/DECISIONS.md D-051]" leaked
# straight onto the public page) until then — `web/templates/web/data_api.html`
# renders a neutral "coming soon" line whenever this is falsy.
SITE_CONTACT_EMAIL = os.environ.get("SWX_CONTACT_EMAIL", "")


def env_int(name, default):
    value = os.environ.get(name, "")
    return int(value) if value.strip() else default


# --- Data access: API keys, limits, export caps (D-083, D-086, D-087) ---------
# Every limit can be overridden from the environment. The per-minute and
# keyless per-hour throttles count in Django's per-process cache, so with
# 2 gunicorn workers they are approximate (up to 2x); the daily quotas are
# counted in the database (api.ApiUsageDay) and are exact.
SWX_API_RATE_PER_MINUTE = env_int("SWX_API_RATE_PER_MINUTE", 60)
SWX_API_DAILY_REQUESTS = env_int("SWX_API_DAILY_REQUESTS", 5000)
SWX_API_DAILY_ROWS = env_int("SWX_API_DAILY_ROWS", 2_000_000)
SWX_API_SERVICE_MULTIPLIER = env_int("SWX_API_SERVICE_MULTIPLIER", 10)
# Keyless preview (D-083): a span of at most this many hours, ending within
# the last SWX_API_KEYLESS_RECENT_DAYS days, at most this many per hour per IP.
SWX_API_KEYLESS_MAX_HOURS = env_int("SWX_API_KEYLESS_MAX_HOURS", 24)
SWX_API_KEYLESS_RECENT_DAYS = env_int("SWX_API_KEYLESS_RECENT_DAYS", 7)
SWX_API_KEYLESS_PER_HOUR = env_int("SWX_API_KEYLESS_PER_HOUR", 30)
# Per-request caps (D-087): span by native cadence, and rows.
SWX_EXPORT_MAX_DAYS_FAST = env_int("SWX_EXPORT_MAX_DAYS_FAST", 31)    # cadence <= 5 min
SWX_EXPORT_MAX_DAYS_SLOW = env_int("SWX_EXPORT_MAX_DAYS_SLOW", 366)   # anything slower
SWX_EXPORT_MAX_ROWS = env_int("SWX_EXPORT_MAX_ROWS", 200_000)
SWX_FORECAST_MAX_DAYS = env_int("SWX_FORECAST_MAX_DAYS", 31)
# Registration (D-086): at most this many new registrations per client IP per day.
SWX_REGISTRATIONS_PER_IP_DAY = env_int("SWX_REGISTRATIONS_PER_IP_DAY", 5)

# --- Nightly NRT export (D-091) ----------------------------------------------
# `manage.py export_nrt` writes the stored rows, per product and UTC day, as
# gzipped JSON lines under this directory, which the off-machine backup pull
# carries away; files older than SWX_NRT_KEEP_DAYS are deleted here.
SWX_NRT_EXPORT_DIR = Path(os.environ.get("SWX_NRT_EXPORT_DIR") or "/srv/swx/backups/nrt")
SWX_NRT_KEEP_DAYS = env_int("SWX_NRT_KEEP_DAYS", 30)

# --- Email (optional, D-086) ---------------------------------------------------
# Only used to send a new API key to its owner. Without SWX_EMAIL_HOST no
# email is ever attempted and the key is only shown on screen.
SWX_EMAIL_HOST = os.environ.get("SWX_EMAIL_HOST", "")
SWX_EMAIL_ENABLED = bool(SWX_EMAIL_HOST)
if SWX_EMAIL_ENABLED:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = SWX_EMAIL_HOST
    EMAIL_PORT = env_int("SWX_EMAIL_PORT", 587)
    EMAIL_HOST_USER = os.environ.get("SWX_EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("SWX_EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = os.environ.get("SWX_EMAIL_USE_TLS", "1") == "1"
DEFAULT_FROM_EMAIL = os.environ.get("SWX_EMAIL_FROM", "") or "noreply@localhost"
EMAIL_TIMEOUT = 10


# --- Django --------------------------------------------------------------------

SECRET_KEY = os.environ.get("SECRET_KEY", "")
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "core",
    "web",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",  # D-008: ES primary, EN secondary
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "climaespacial.urls"
WSGI_APPLICATION = "climaespacial.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "web.context_processors.site",
                "web.context_processors.strip",
            ],
        },
    },
]

if not os.environ.get("DATABASE_URL"):
    raise ImproperlyConfigured("DATABASE_URL is not set (see .env.example).")
DATABASES = {"default": database_from_url(os.environ["DATABASE_URL"])}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# --- i18n (D-008: Spanish primary, English secondary) -------------------------

LANGUAGE_CODE = "es"
LANGUAGES = [("es", "Español"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "America/Santiago"
USE_I18N = True
USE_TZ = True

STATIC_URL = f"{SWX_SCRIPT_NAME}/static/" if SWX_SCRIPT_NAME else "static/"
STATIC_ROOT = os.environ.get("STATIC_ROOT") or BASE_DIR / "staticfiles"
# web/static/ is found via APP_DIRS (the "web" app) — no extra STATICFILES_DIRS entry.

# D-055: hashed static filenames (base.3f9c2a.css), so a release can never
# pair new HTML with a browser's cached old CSS/JS again (13-AUDIT §1) and
# nginx can serve /static/ as `immutable`. `collectstatic` writes the
# manifest; settings.dev swaps in the plain storage so runserver and the
# test suite never need one.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
}

# D-057: per-process memory cache for the `serie.json` responses (60 s) —
# no Redis on this VM (D-002). Each gunicorn worker keeps its own copy,
# which only means up to one query per worker per minute per (product, range).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "swx",
        "OPTIONS": {"MAX_ENTRIES": 500},
    }
}
