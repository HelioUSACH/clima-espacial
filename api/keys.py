"""API key format, hashing and lookup (D-086).

A key is `swx_` + `secrets.token_urlsafe(32)` (43 URL-safe characters), so
it can travel in a query string untouched. Only its SHA-256 is stored; the
plaintext exists in memory long enough to show it once (and, if SMTP is
configured, to email it), then it is gone. A lost key can't be recovered,
only replaced.
"""

from __future__ import annotations

import hashlib
import secrets

from django.utils import timezone

from .models import ApiKey

KEY_PREFIX = "swx_"
DISPLAY_CHARS = 12  # "swx_" + 8: enough to tell keys apart in the admin, useless to an attacker


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_key_string() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def issue_key(*, kind: str = "user", registration=None, label: str = "", daily_requests=None,
              daily_rows=None) -> tuple[ApiKey, str]:
    """Create a key and return (row, plaintext). The plaintext is never
    stored: show it now or lose it."""
    raw = new_key_string()
    key = ApiKey.objects.create(prefix=raw[:DISPLAY_CHARS], key_hash=hash_key(raw), kind=kind,
                                registration=registration, label=label, daily_requests=daily_requests,
                                daily_rows=daily_rows)
    return key, raw


def find_key(raw: str) -> ApiKey | None:
    """The active key whose hash matches `raw`, or None (unknown, malformed
    or revoked: the caller can't tell which, on purpose)."""
    raw = (raw or "").strip()
    if not raw.startswith(KEY_PREFIX) or len(raw) > 128:
        return None
    return ApiKey.objects.filter(key_hash=hash_key(raw), active=True).select_related("registration").first()


def revoke(queryset) -> int:
    return queryset.filter(active=True).update(active=False, revoked_at=timezone.now())
