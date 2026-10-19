"""Registration issues a key shown once (D-086), optional email, the admin
side (issue key, service key, revoke) and the usage report."""

import datetime as dt
import io
import re

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from api import access
from api.keys import hash_key, issue_key
from api.models import ApiKey
from web.models import RegistrationRequest

FORM = {"name": "Ana Pérez", "email": "ana@example.org", "institution": "Una empresa", "sector": "power",
        "intended_use": "GIC", "consent_given": "on"}
KEY_RE = re.compile(r"swx_[A-Za-z0-9_-]{43}")


def _register(client, **overrides):
    return client.post("/datos-api/", {**FORM, **overrides})


@pytest.mark.django_db
def test_registration_shows_the_key_once_in_the_post_response(client, settings):
    settings.SWX_EMAIL_ENABLED = False
    r = _register(client)
    assert r.status_code == 200 and r["Cache-Control"] == "no-store"
    html = r.content.decode()
    keys = set(KEY_RE.findall(html))
    assert len(keys) == 1
    raw = keys.pop()
    key = ApiKey.objects.get()
    assert key.key_hash == hash_key(raw) and key.kind == "user"
    assert key.registration == RegistrationRequest.objects.get(email="ana@example.org")
    assert 'data-copy="api-key"' in html and "X-API-Key" in html and "/hapi/data?" in html
    assert "correo" not in html.lower().split("primeros pasos")[0]  # no email mentioned when SMTP is off
    assert len(mail.outbox) == 0
    # Nowhere else to get it back from: the page is the only copy.
    assert raw not in client.get("/datos-api/").content.decode()


@pytest.mark.django_db
def test_same_email_within_ten_minutes_gets_no_new_key(client):
    _register(client)
    r = _register(client, email="ANA@example.org")
    assert r.status_code == 200 and not KEY_RE.search(r.content.decode())
    assert "hace unos minutos" in r.content.decode()
    assert ApiKey.objects.count() == 1 and RegistrationRequest.objects.count() == 1
    RegistrationRequest.objects.update(created_at=timezone.now() - dt.timedelta(minutes=11))
    assert KEY_RE.search(_register(client).content.decode())
    assert ApiKey.objects.count() == 2


@pytest.mark.django_db
def test_honeypot_saves_nothing(client):
    r = _register(client, website="http://spam.example")
    assert r.status_code == 400
    assert not RegistrationRequest.objects.exists() and not ApiKey.objects.exists()


@pytest.mark.django_db
def test_registrations_per_ip_per_day(client, settings):
    settings.SWX_REGISTRATIONS_PER_IP_DAY = 2
    for i in range(2):
        assert KEY_RE.search(_register(client, email=f"p{i}@example.org").content.decode())
    r = _register(client, email="p3@example.org")
    assert r.status_code == 429 and not KEY_RE.search(r.content.decode())
    assert RegistrationRequest.objects.count() == 2


@pytest.mark.django_db
def test_consent_still_required(client):
    data = dict(FORM)
    data.pop("consent_given")
    r = client.post("/datos-api/", data)
    assert r.status_code == 200 and not ApiKey.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("lang,subject_word", [("es", "llave"), ("en", "key")])
def test_key_is_emailed_in_the_page_language_when_smtp_is_set(client, settings, lang, subject_word):
    settings.SWX_EMAIL_ENABLED = True
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    r = client.post(("/en" if lang == "en" else "") + "/datos-api/", FORM)
    raw = KEY_RE.search(r.content.decode()).group(0)
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["ana@example.org"] and subject_word in message.subject.lower()
    assert raw in message.body and "X-API-Key" in message.body
    assert "ana@example.org" in r.content.decode()
    assert RegistrationRequest.objects.get().language == lang


@pytest.mark.django_db
def test_failed_email_never_blocks_the_page(client, settings, monkeypatch, caplog):
    settings.SWX_EMAIL_ENABLED = True

    def boom(*args, **kwargs):
        raise OSError("SMTP down")

    monkeypatch.setattr("api.emails.send_mail", boom)
    r = _register(client)
    html = r.content.decode()
    assert r.status_code == 200 and KEY_RE.search(html)
    assert "No pudimos enviarte la llave por correo" in html
    assert "Could not send the API key email" in caplog.text


# --- admin --------------------------------------------------------------------------

@pytest.fixture
def admin_client(client, db):
    User.objects.create_superuser("admin", "admin@example.org", "a-long-test-password")
    client.login(username="admin", password="a-long-test-password")
    return client


@pytest.mark.django_db
def test_admin_issue_key_for_an_old_registration(admin_client, settings):
    settings.SWX_EMAIL_ENABLED = True
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    reg = RegistrationRequest.objects.create(name="Old", email="old@example.org", institution="U", sector="research",
                                             consent_given=True)
    r = admin_client.post("/gestion/web/registrationrequest/", {"action": "issue_keys", "_selected_action": [reg.pk]},
                          follow=True)
    html = r.content.decode()
    raw = KEY_RE.search(html).group(0)
    key = ApiKey.objects.get(registration=reg)
    assert key.key_hash == hash_key(raw) and "Emailed to them" in html
    assert mail.outbox[0].to == ["old@example.org"]


@pytest.mark.django_db
def test_admin_creates_a_service_key_shown_once(admin_client):
    r = admin_client.post("/gestion/api/apikey/add/", {"label": "national platform", "kind": "service",
                                                        "daily_requests": "100000", "daily_rows": ""}, follow=True)
    html = r.content.decode()
    raw = KEY_RE.search(html).group(0)
    key = ApiKey.objects.get()
    assert (key.kind, key.label, key.daily_requests, key.daily_rows) == ("service", "national platform", 100000, None)
    assert key.key_hash == hash_key(raw)
    assert raw not in admin_client.get(f"/gestion/api/apikey/{key.pk}/change/").content.decode()


@pytest.mark.django_db
def test_admin_list_shows_sector_and_30_day_usage_and_revokes(admin_client):
    reg = RegistrationRequest.objects.create(name="N", email="n@example.org", institution="U", sector="mining_gnss",
                                             consent_given=True)
    key, _raw = issue_key(registration=reg)
    access.record_usage(key, "hapi", 1234)
    html = admin_client.get("/gestion/api/apikey/").content.decode()
    assert "Minería / GNSS" in html and "1234" in html
    admin_client.post("/gestion/api/apikey/", {"action": "revoke_keys", "_selected_action": [key.pk]})
    key.refresh_from_db()
    assert key.active is False and key.revoked_at is not None


# --- usage report ---------------------------------------------------------------------

@pytest.mark.django_db
def test_api_usage_report_by_sector_without_personal_data():
    when = dt.datetime(2026, 10, 18, 12, tzinfo=dt.UTC)
    power = RegistrationRequest.objects.create(name="Secret Name", email="secret@example.org", institution="X",
                                               sector="power", consent_given=True)
    RegistrationRequest.objects.filter(pk=power.pk).update(created_at=when)
    user_key, _ = issue_key(registration=power)
    service_key, _ = issue_key(kind="service", label="partner")
    access.record_usage(user_key, "hapi", 100, now=when)
    access.record_usage(user_key, "download", 50, now=when)
    access.record_usage(service_key, "hapi", 1000, now=when)
    access.record_usage(None, "hapi", 10, now=when)
    access.record_usage(None, "hapi", 10, now=when + dt.timedelta(days=20))
    out = io.StringIO()
    call_command("api_usage_report", "--from", "2026-10-01", "--to", "2026-11-30", stdout=out)
    text = out.getvalue()
    assert "Secret" not in text and "secret@" not in text
    assert text.splitlines() == [
        "period,sector,active_keys,requests,rows,new_registrations",
        "2026-10,power,1,2,150,1",
        "2026-10,service,1,1,1000,0",
        "2026-10,anonymous,0,1,10,0",
        "2026-11,anonymous,0,1,10,0",
    ]
    out = io.StringIO()
    call_command("api_usage_report", "--from", "2026-10-18", "--to", "2026-10-18", "--by", "day", stdout=out)
    assert out.getvalue().splitlines()[1] == "2026-10-18,power,1,2,150,1"
