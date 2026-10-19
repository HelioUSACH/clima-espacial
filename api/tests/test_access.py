"""Keys, limits, the keyless preview and usage counting (D-083, D-086)."""

import datetime as dt
import hashlib

import pytest
from django.test import RequestFactory
from django.utils import timezone

from api import access
from api.keys import find_key, hash_key, issue_key, new_key_string, revoke
from api.models import ApiKey, ApiUsageDay

rf = RequestFactory()
NOW = dt.datetime(2026, 10, 18, 12, 30, 15, tzinfo=dt.UTC)


def test_key_format():
    raw = new_key_string()
    assert raw.startswith("swx_") and len(raw) == 4 + 43
    assert raw != new_key_string()


@pytest.mark.django_db
def test_only_the_hash_is_stored_and_lookup_finds_it():
    key, raw = issue_key(kind="user", label="x")
    assert key.key_hash == hashlib.sha256(raw.encode()).hexdigest() == hash_key(raw)
    assert key.prefix == raw[:12]
    stored = ApiKey.objects.filter(pk=key.pk).values()[0]
    assert raw not in [str(v) for v in stored.values()]
    assert find_key(raw) == key
    assert find_key(raw + "x") is None
    assert find_key("not-a-key") is None
    assert find_key("") is None


@pytest.mark.django_db
def test_header_and_query_parameter_are_both_accepted(key):
    obj, raw = key
    assert access.authorize(rf.get("/", HTTP_X_API_KEY=raw), preview_ok=False) == obj
    assert access.authorize(rf.get("/", {"api_key": raw}), preview_ok=False) == obj
    # The header wins over the query parameter.
    assert access.authorize(rf.get("/", {"api_key": "swx_bogus"}, HTTP_X_API_KEY=raw), preview_ok=False) == obj


@pytest.mark.django_db
def test_revoked_or_unknown_key_is_401(key):
    obj, raw = key
    assert revoke(ApiKey.objects.filter(pk=obj.pk)) == 1
    obj.refresh_from_db()
    assert obj.active is False and obj.revoked_at is not None
    for request in (rf.get("/", HTTP_X_API_KEY=raw), rf.get("/", HTTP_X_API_KEY="swx_" + "a" * 43)):
        with pytest.raises(access.Denied) as exc:
            access.authorize(request, preview_ok=True)  # a bad key is refused even inside the preview
        assert exc.value.status == 401
        assert "/datos-api/" in exc.value.message


@pytest.mark.django_db
def test_no_key_outside_the_preview_is_401_pointing_to_registration():
    with pytest.raises(access.Denied) as exc:
        access.authorize(rf.get("/"), preview_ok=False)
    assert exc.value.status == 401 and "http://testserver/datos-api/" in exc.value.message


def test_preview_boundaries():
    stop = NOW
    assert access.is_preview(stop - dt.timedelta(hours=24), stop, now=NOW)
    assert not access.is_preview(stop - dt.timedelta(hours=24, seconds=1), stop, now=NOW)
    old_stop = NOW - dt.timedelta(days=7)
    assert access.is_preview(old_stop - dt.timedelta(hours=1), old_stop, now=NOW)
    older_stop = old_stop - dt.timedelta(seconds=1)
    assert not access.is_preview(older_stop - dt.timedelta(hours=1), older_stop, now=NOW)


@pytest.mark.django_db
def test_keyless_preview_is_throttled_per_ip(settings):
    settings.SWX_API_KEYLESS_PER_HOUR = 3
    for _ in range(3):
        assert access.authorize(rf.get("/", HTTP_X_REAL_IP="203.0.113.5"), preview_ok=True, now=NOW) is None
    with pytest.raises(access.Denied) as exc:
        access.authorize(rf.get("/", HTTP_X_REAL_IP="203.0.113.5"), preview_ok=True, now=NOW)
    assert exc.value.status == 429 and exc.value.retry_after == 3600 - (30 * 60 + 15)
    # Another client is not affected, nor is the next hour.
    assert access.authorize(rf.get("/", HTTP_X_REAL_IP="203.0.113.6"), preview_ok=True, now=NOW) is None
    assert access.authorize(rf.get("/", HTTP_X_REAL_IP="203.0.113.5"), preview_ok=True,
                            now=NOW + dt.timedelta(hours=1)) is None


@pytest.mark.django_db
def test_per_minute_rate_limit(settings, key):
    settings.SWX_API_RATE_PER_MINUTE = 2
    _obj, raw = key
    for _ in range(2):
        access.authorize(rf.get("/", HTTP_X_API_KEY=raw), preview_ok=False, now=NOW)
    with pytest.raises(access.Denied) as exc:
        access.authorize(rf.get("/", HTTP_X_API_KEY=raw), preview_ok=False, now=NOW)
    assert exc.value.status == 429 and exc.value.retry_after == 45


@pytest.mark.django_db
def test_daily_request_quota_counts_the_utc_day(settings, key):
    settings.SWX_API_DAILY_REQUESTS = 2
    obj, raw = key
    access.record_usage(obj, "hapi", 10, now=NOW)
    access.record_usage(obj, "download", 10, now=NOW)
    with pytest.raises(access.Denied) as exc:
        access.authorize(rf.get("/", HTTP_X_API_KEY=raw), preview_ok=False, now=NOW)
    assert exc.value.status == 429
    assert exc.value.retry_after == int((dt.datetime(2026, 10, 19, tzinfo=dt.UTC) - NOW).total_seconds())
    assert access.authorize(rf.get("/", HTTP_X_API_KEY=raw), preview_ok=False,
                            now=NOW + dt.timedelta(days=1)) == obj


@pytest.mark.django_db
def test_daily_row_quota_includes_the_request_itself(settings, key):
    settings.SWX_API_DAILY_ROWS = 1000
    obj, raw = key
    access.record_usage(obj, "hapi", 900, now=NOW)
    assert access.authorize(rf.get("/", HTTP_X_API_KEY=raw), preview_ok=False, rows=100, now=NOW) == obj
    with pytest.raises(access.Denied) as exc:
        access.authorize(rf.get("/", HTTP_X_API_KEY=raw), preview_ok=False, rows=101, now=NOW)
    assert exc.value.status == 429 and "rows" in exc.value.message


@pytest.mark.django_db
def test_service_keys_get_ten_times_and_per_key_quotas_override(settings):
    settings.SWX_API_DAILY_REQUESTS = 5
    service, _ = issue_key(kind="service", label="national platform")
    assert access.limits(service) == (settings.SWX_API_RATE_PER_MINUTE * 10, 50, settings.SWX_API_DAILY_ROWS * 10)
    custom, _ = issue_key(kind="user", daily_requests=7, daily_rows=70)
    assert access.limits(custom)[1:] == (7, 70)


@pytest.mark.django_db
def test_usage_is_one_row_per_key_day_endpoint_incremented_in_place(key):
    obj, _raw = key
    for rows in (10, 20, 30):
        access.record_usage(obj, "hapi", rows, now=NOW)
    access.record_usage(obj, "download", 5, now=NOW)
    access.record_usage(obj, "hapi", 1, now=NOW + dt.timedelta(days=1))
    for _ in range(4):
        access.record_usage(None, "hapi", 2, now=NOW)  # anonymous: one row too (NULLS NOT DISTINCT)
    rows = {(u.key_id, u.day.isoformat(), u.endpoint): (u.requests, u.rows) for u in ApiUsageDay.objects.all()}
    assert rows == {
        (obj.pk, "2026-10-18", "hapi"): (3, 60),
        (obj.pk, "2026-10-18", "download"): (1, 5),
        (obj.pk, "2026-10-19", "hapi"): (1, 1),
        (None, "2026-10-18", "hapi"): (4, 8),
    }
    obj.refresh_from_db()
    assert obj.last_used_at == NOW + dt.timedelta(days=1)


def test_client_ip_prefers_nginx_header():
    assert access.client_ip(rf.get("/", HTTP_X_REAL_IP="198.51.100.7", REMOTE_ADDR="127.0.0.1")) == "198.51.100.7"
    assert access.client_ip(rf.get("/", REMOTE_ADDR="127.0.0.1")) == "127.0.0.1"


@pytest.mark.django_db
def test_now_default_is_aware():
    # authorize/record_usage without `now` use the real clock.
    access.record_usage(None, "forecast", 1)
    assert ApiUsageDay.objects.get().day == timezone.now().astimezone(dt.UTC).date()
