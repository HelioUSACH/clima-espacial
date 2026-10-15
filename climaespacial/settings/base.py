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
# registrations.
SITE_CONTACT_EMAIL = os.environ.get("SWX_CONTACT_EMAIL", "[pendiente — ver docs/DECISIONS.md D-051]")


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
