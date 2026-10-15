"""Staging and production. HTTPS-only protections switch on with SWX_HTTPS=1."""

from .base import *  # noqa: F401,F403
from .base import ALLOWED_HOSTS, SECRET_KEY, SWX_AMBIENTE, SWX_HTTPS, ImproperlyConfigured

if SWX_AMBIENTE == "dev":
    raise ImproperlyConfigured("settings.prod needs SWX_AMBIENTE=staging or produccion.")
if SWX_AMBIENTE == "produccion" and not SWX_HTTPS:
    raise ImproperlyConfigured("produccion requires SWX_HTTPS=1.")
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY is not set.")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS is not set.")

DEBUG = False

# nginx terminates TLS and always sets X-Forwarded-Proto itself.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if SWX_HTTPS:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # One hour while HTTPS is new; raise to 31536000 once it has run without trouble.
    SECURE_HSTS_SECONDS = 3600
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    # W021 asks for HSTS preload; skip until the HTTPS rollout has proven stable.
    SILENCED_SYSTEM_CHECKS = ["security.W021"]

# Django's default sends errors only by email when DEBUG is off. Send them to journald instead.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"django": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}
