"""Who may make a data request, and counting what they did (D-083, D-086).

Every data endpoint (HAPI `data`, CSV downloads, forecast export) does the
same three steps, in this order, before streaming anything:

1. work out the request (product, range, row count) and whether it fits the
   keyless preview;
2. `authorize()`: a key from `X-API-Key` (preferred) or `?api_key=` (HAPI
   clients can't set headers), checked against the per-minute rate and the
   daily request/row quotas; or, without a key, the preview's per-IP
   throttle. Raises `Denied` (401/429) otherwise;
3. `record_usage()`: one upsert into `ApiUsageDay` (key or anonymous).

Metadata (HAPI about/capabilities/catalog/info) needs no key and isn't
counted.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone, translation

from .keys import find_key
from .models import ApiKey, ApiUsageDay

KEY_HEADER = "HTTP_X_API_KEY"
KEY_PARAM = "api_key"


class Denied(Exception):
    """A request that may not be served: HTTP `status` (401 or 429), a
    message for the client, and `retry_after` seconds for a 429."""

    def __init__(self, status: int, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


def raw_key(request) -> str:
    return (request.META.get(KEY_HEADER) or request.GET.get(KEY_PARAM) or "").strip()


def client_ip(request) -> str:
    # nginx sets X-Real-IP to $remote_addr, overwriting anything the client
    # sent; without nginx (dev, tests) REMOTE_ADDR is the client.
    return (request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR") or "").strip()


def registration_url(request) -> str:
    with translation.override("es"):
        return request.build_absolute_uri(reverse("web:data_api"))


def is_preview(start: dt.datetime, stop: dt.datetime, now: dt.datetime | None = None) -> bool:
    """D-083: a span of at most SWX_API_KEYLESS_MAX_HOURS that ends within
    the last SWX_API_KEYLESS_RECENT_DAYS days (or later)."""
    now = now or timezone.now()
    return (stop - start <= dt.timedelta(hours=settings.SWX_API_KEYLESS_MAX_HOURS)
            and stop >= now - dt.timedelta(days=settings.SWX_API_KEYLESS_RECENT_DAYS))


def limits(key: ApiKey) -> tuple[int, int, int]:
    """(requests per minute, requests per UTC day, rows per UTC day)."""
    factor = settings.SWX_API_SERVICE_MULTIPLIER if key.kind == "service" else 1
    per_minute = settings.SWX_API_RATE_PER_MINUTE * factor
    per_day = key.daily_requests if key.daily_requests is not None else settings.SWX_API_DAILY_REQUESTS * factor
    rows = key.daily_rows if key.daily_rows is not None else settings.SWX_API_DAILY_ROWS * factor
    return per_minute, per_day, rows


def _seconds_to_utc_midnight(now: dt.datetime) -> int:
    tomorrow = (now.astimezone(dt.UTC) + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


def _hit(cache_key: str, window: int) -> int:
    """Count one hit in a fixed window of the per-process cache (approximate
    across gunicorn workers, D-086). Returns the count including this one."""
    if cache.add(cache_key, 1, window + 5):
        return 1
    try:
        return cache.incr(cache_key)
    except ValueError:  # expired between add() and incr()
        cache.set(cache_key, 1, window + 5)
        return 1


def authorize(request, *, preview_ok: bool, rows: int = 0, now: dt.datetime | None = None) -> ApiKey | None:
    """The key the request is made with, or None for an allowed keyless
    preview. Raises Denied otherwise. `rows` is what the request would
    return, checked against the key's daily row quota."""
    now = now or timezone.now()
    raw = raw_key(request)
    if raw:
        key = find_key(raw)
        if key is None:
            raise Denied(401, "Invalid or revoked API key. Register for a free key at " + registration_url(request))
        per_minute, per_day, rows_per_day = limits(key)
        minute = int(now.timestamp()) // 60
        if _hit(f"swx:api:rate:{key.pk}:{minute}", 60) > per_minute:
            raise Denied(429, f"Rate limit: at most {per_minute} requests per minute for this key.",
                         retry_after=60 - now.second)
        used = ApiUsageDay.objects.filter(key=key, day=now.astimezone(dt.UTC).date()).aggregate(
            requests=Sum("requests"), rows=Sum("rows"))
        if (used["requests"] or 0) >= per_day:
            raise Denied(429, f"Daily quota: at most {per_day} requests per UTC day for this key.",
                         retry_after=_seconds_to_utc_midnight(now))
        if (used["rows"] or 0) + rows > rows_per_day:
            raise Denied(429, f"Daily quota: at most {rows_per_day} rows per UTC day for this key "
                              f"({used['rows'] or 0} used, this request would add {rows}).",
                         retry_after=_seconds_to_utc_midnight(now))
        return key
    if not preview_ok:
        raise Denied(401, (
            f"This request needs an API key: without one, a request may span at most "
            f"{settings.SWX_API_KEYLESS_MAX_HOURS} h and end within the last "
            f"{settings.SWX_API_KEYLESS_RECENT_DAYS} days (forecasts: the latest issue only). "
            f"Register for a free key at {registration_url(request)} and send it as the X-API-Key header "
            f"or the api_key parameter."))
    hour = int(now.timestamp()) // 3600
    if _hit(f"swx:api:anon:{client_ip(request)}:{hour}", 3600) > settings.SWX_API_KEYLESS_PER_HOUR:
        raise Denied(429, (f"Keyless requests are limited to {settings.SWX_API_KEYLESS_PER_HOUR} per hour. "
                           f"Register for a free key at {registration_url(request)}."),
                     retry_after=3600 - int(now.timestamp()) % 3600)
    return None


_UPSERT_SQL = (
    "INSERT INTO api_apiusageday (key_id, day, endpoint, requests, rows) VALUES (%s, %s, %s, 1, %s) "
    "ON CONFLICT (key_id, day, endpoint) DO UPDATE "
    "SET requests = api_apiusageday.requests + 1, rows = api_apiusageday.rows + EXCLUDED.rows"
)


def record_usage(key: ApiKey | None, endpoint: str, rows: int, now: dt.datetime | None = None) -> None:
    """One served request: +1 request and +`rows` rows on today's (key,
    endpoint) row, in one statement, so concurrent requests never lose a
    count. Keyless requests count under key = NULL."""
    now = now or timezone.now()
    with connection.cursor() as cursor:
        cursor.execute(_UPSERT_SQL, [key.pk if key else None, now.astimezone(dt.UTC).date(), endpoint, rows])
    if key is not None:
        ApiKey.objects.filter(pk=key.pk).update(last_used_at=now)
