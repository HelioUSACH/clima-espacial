"""v0.6.4 (D-119..D-121): the stale-product e-mail (`manage.py
check_freshness`, web/freshness.py), the flare page's notice when its
newest valid result is old, and the difference tiles loading with the page.
The notice in the browser (both languages, themes and widths) is
screenshotted in CI against the stub's `stale` scenario."""

import datetime as dt
import io
import re

import pytest
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from core import swxkit
from core.models import Heartbeat
from web import flare, freshness
from web.models import FreshnessAlert
from web.tests.test_flare_page import PAGE, FakeService, _iso, _publish_v2, _slot_now

KP = "noaa.kp"
TO = ["alertas@example.org", "otra@example.org"]
QUALITY = ["quality:131=1073741828", "quality:1600=1073741828"]


@pytest.fixture
def smtp(settings):
    settings.SWX_EMAIL_ENABLED = True
    settings.SWX_ALERT_EMAILS = TO
    settings.SWX_SITE_ORIGIN = "https://portal.example.org"
    settings.SWX_SCRIPT_NAME = "/clima-espacial"


@pytest.fixture
def service(monkeypatch):
    fake = FakeService()
    monkeypatch.setattr(flare.httpx, "get", fake.get)
    return fake


def _kp(age_s=0):
    hb = swxkit.heartbeat(KP, "ok")
    Heartbeat.objects.filter(pk=hb.pk).update(last_seen_at=timezone.now() - dt.timedelta(seconds=age_s))
    return hb.product


def _stale_kp():
    _kp(age_s=_kp().stale_after_seconds + 600)


def _run(**kw):
    from django.core.cache import cache
    cache.clear()  # the flare service's 30 s cache: each run reads it afresh
    return freshness.run(**kw)


def _hour_passes(product_id):
    FreshnessAlert.objects.filter(product_id=product_id).update(
        last_mail_at=timezone.now() - dt.timedelta(minutes=61))


# --- the state machine ----------------------------------------------------------

@pytest.mark.django_db
def test_first_run_records_the_state_and_sends_nothing(smtp):
    _stale_kp()
    outcomes = _run()
    assert [o.action for o in outcomes] == ["first"]
    assert mail.outbox == []
    row = FreshnessAlert.objects.get(product_id=KP)
    assert (row.state, row.notified, row.last_mail_at) == ("stale", "stale", None)
    _run()
    assert mail.outbox == []  # stale at deploy time: never announced


@pytest.mark.django_db
def test_stale_one_mail_then_none_then_one_recovery(smtp):
    _kp()
    _run()
    _stale_kp()
    _run()
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == TO
    assert msg.subject.startswith("[Clima Espacial USACH] Desactualizado: ")
    assert "(noaa.kp)" in msg.body and "Estado: desactualizado" in msg.body
    assert re.search(r"Desde: \d{4}-\d\d-\d\d \d\d:\d\d UT", msg.body)
    assert "Último dato: " in msg.body
    assert "https://portal.example.org/clima-espacial/estado/" in msg.body
    # Still stale: nothing, inside the hour and after it.
    _run()
    _hour_passes(KP)
    _run()
    assert len(mail.outbox) == 1
    # Back, and the last mail was over an hour ago: one recovery mail.
    _kp()
    _run()
    assert len(mail.outbox) == 2
    assert mail.outbox[1].subject.startswith("[Clima Espacial USACH] De nuevo al día: ")
    assert "Estuvo desactualizado desde: " in mail.outbox[1].body
    _run()
    assert len(mail.outbox) == 2
    row = FreshnessAlert.objects.get(product_id=KP)
    assert (row.state, row.notified, row.since) == ("ok", "ok", None)


@pytest.mark.django_db
def test_never_more_than_one_mail_an_hour(smtp):
    _kp()
    _run()
    _stale_kp()
    _run()
    _kp()                       # back within minutes: the recovery mail waits
    assert [o.action for o in _run()] == ["wait"]
    _stale_kp()                 # and stale again: nothing new to say
    assert [o.action for o in _run()] == [""]
    _kp()
    _run()
    assert len(mail.outbox) == 1
    _hour_passes(KP)            # an hour after the first mail, still back: now it goes
    _run()
    assert len(mail.outbox) == 2 and "De nuevo al día" in mail.outbox[1].subject


@pytest.mark.django_db
def test_error_counts_as_stale_and_no_data_is_never_mailed(smtp):
    _kp()
    _run()
    swxkit.heartbeat(KP, "error", detail="SWPC answered 503")
    _run()
    assert len(mail.outbox) == 1
    assert "Estado: con errores" in mail.outbox[0].body and "SWPC answered 503" in mail.outbox[0].body


@pytest.mark.django_db
@pytest.mark.parametrize("change", [{"SWX_ALERT_EMAILS": []}, {"SWX_EMAIL_ENABLED": False}])
def test_no_recipients_or_no_smtp_sends_nothing(smtp, settings, change, caplog):
    for key, value in change.items():
        setattr(settings, key, value)
    _kp()
    _run()
    _stale_kp()
    outcomes = _run()
    assert outcomes[0].result == freshness.OFF
    assert mail.outbox == []
    assert "not e-mailed" in caplog.text
    # Recorded as told: configuring SMTP later doesn't mail an old change.
    assert FreshnessAlert.objects.get(product_id=KP).notified == "stale"


@pytest.mark.django_db
def test_a_failed_send_is_logged_never_raised_and_retried(smtp, monkeypatch, caplog):
    _kp()
    _run()
    _stale_kp()

    def broken(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(freshness, "send_mail", broken)
    assert _run()[0].result == freshness.FAILED
    assert "Could not send the freshness alert" in caplog.text
    assert FreshnessAlert.objects.get(product_id=KP).notified == "ok"
    monkeypatch.undo()
    _run()
    assert len(mail.outbox) == 1


# --- the flare product: its newest valid result ---------------------------------

def _flare_status(fake, *, last_ok, streak=0, reasons=()):
    status = fake.payloads["/api/status"]
    status.update({"last_ok_slot": _iso(last_ok) if last_ok else None, "rejected_streak": streak,
                   "rejected_since": _iso(last_ok + dt.timedelta(minutes=2)) if streak and last_ok else None,
                   "last_reject_reasons": list(reasons)})


@pytest.mark.django_db
def test_flare_stale_mail_has_the_reject_reasons(smtp, service):
    now = _slot_now()
    _publish_v2(valid_from=now - dt.timedelta(minutes=10))
    _flare_status(service, last_ok=now - dt.timedelta(minutes=10))
    _run()
    assert FreshnessAlert.objects.get(product_id=flare.PRODUCT_ID).state == "ok"
    # flare-nrt goes on answering, but rejects every frame: the collector's
    # heartbeat stays fresh, the newest valid result does not.
    old = now - dt.timedelta(minutes=50)
    FreshnessAlert.objects.filter(product_id=flare.PRODUCT_ID).update(state="ok")
    from core.models import Event
    Event.objects.filter(product_id=flare.PRODUCT_ID).update(valid_from=old)
    swxkit.heartbeat(flare.PRODUCT_ID, "ok", detail="no new result since the last poll")
    _flare_status(service, last_ok=old, streak=20, reasons=QUALITY)
    _run()
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert f"Desde: {freshness.fmt_when(old + flare.RESULT_STALE_AFTER)}" in body
    assert f"Último dato: {freshness.fmt_when(old)} (resultado válido más reciente" in body
    assert "20 imágenes seguidas sin resultado" in body
    assert "quality:131=1073741828, quality:1600=1073741828" in body
    assert "las imágenes de SDO/AIA llegan marcadas con problemas de calidad" in body


@pytest.mark.django_db
def test_flare_uses_last_ok_slot_when_newer_than_the_store(smtp, service):
    now = _slot_now()
    _publish_v2(valid_from=now - dt.timedelta(hours=2))
    _flare_status(service, last_ok=now - dt.timedelta(minutes=4))
    check = freshness.check_flare(flare_product(), timezone.now())
    assert check.state == "ok"
    assert check.last_datum == now - dt.timedelta(minutes=4)
    # flare-nrt < 1.3.1 (no last_ok_slot): the stored result decides.
    service.payloads["/api/status"].pop("last_ok_slot")
    from django.core.cache import cache
    cache.clear()
    assert freshness.check_flare(flare_product(), timezone.now()).state == "stale"
    # Service down: the stored result, and the mail says so.
    service.down = True
    cache.clear()
    check = freshness.check_flare(flare_product(), timezone.now())
    assert check.state == "stale" and "flare-nrt no responde (/api/status)." in check.notes


def flare_product():
    from core.models import Product
    return Product.objects.get(pk=flare.PRODUCT_ID)


# --- the command ------------------------------------------------------------------

@pytest.mark.django_db
def test_dry_run_prints_and_changes_nothing(smtp):
    _kp()
    _run()
    _stale_kp()
    out = io.StringIO()
    call_command("check_freshness", "--dry-run", stdout=out)
    text = out.getvalue()
    assert "Dry run. E-mail: on" in text
    assert "noaa.kp: desactualizado" in text and "would send the stale mail" in text
    assert "Subject: [Clima Espacial USACH] Desactualizado:" in text
    assert mail.outbox == []
    assert FreshnessAlert.objects.get(product_id=KP).state == "ok"


@pytest.mark.django_db
def test_dry_run_on_a_fresh_database():
    _kp()
    out = io.StringIO()
    call_command("check_freshness", "--dry-run", stdout=out)
    assert "E-mail: off, SMTP is not configured" in out.getvalue()
    assert "first check: state recorded, no mail" in out.getvalue()
    assert not FreshnessAlert.objects.exists()


@pytest.mark.django_db
def test_test_email(smtp):
    out = io.StringIO()
    call_command("check_freshness", "--test-email", stdout=out)
    assert len(mail.outbox) == 1 and mail.outbox[0].to == TO
    assert "Correo de prueba" in mail.outbox[0].subject
    assert "Test e-mail sent to alertas@example.org, otra@example.org." in out.getvalue()


@pytest.mark.django_db
def test_test_email_without_smtp(settings):
    settings.SWX_ALERT_EMAILS = TO
    err = io.StringIO()
    call_command("check_freshness", "--test-email", stderr=err)
    assert mail.outbox == [] and "Not sent: SMTP is not configured" in err.getvalue()


def test_the_timer_runs_it_every_5_minutes():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "deploy" / "systemd"
    assert "manage.py check_freshness" in (root / "swx-freshness.service").read_text()
    assert "OnUnitActiveSec=5min" in (root / "swx-freshness.timer").read_text()


# --- the page ----------------------------------------------------------------------

@pytest.fixture
def public(settings):
    settings.SWX_FLARE_PAGE_PUBLIC = True


@pytest.mark.parametrize("codes, phrases", [
    (QUALITY, ["las imágenes de SDO/AIA llegan marcadas con problemas de calidad"]),
    (["missing:171", "missing:304"], ["no han llegado imágenes de SDO/AIA"]),
    (["median_jump:131", "quality:131=1"], ["las imágenes de SDO/AIA no pasan el control de calidad del detector",
                                             "las imágenes de SDO/AIA llegan marcadas con problemas de calidad"]),
    (["baseline_missing"], ["faltan las imágenes de 8 a 12 minutos antes, con las que se compara cada una"]),
    (["inputs_old"], ["las imágenes de SDO/AIA llegan con retraso"]),
    (["something_new:1", "error"], ["el detector no ha podido procesar las imágenes recientes"]),
    ("not a list", []),
])
def test_reason_codes_to_words(codes, phrases):
    assert flare.reject_reasons(codes) == phrases


@pytest.mark.django_db
def test_page_notice_when_the_newest_result_is_old(client, service, public):
    now = _slot_now()
    old = now - dt.timedelta(hours=2)
    _publish_v2(valid_from=old)
    _flare_status(service, last_ok=old, streak=55, reasons=QUALITY)
    html = client.get(PAGE).content.decode()
    note = re.search(r'<p class="flare-note flare-stale"[^>]*data-flare-stale>(.*?)</p>', html, re.S)
    assert note, "no stale notice"
    assert "Sin resultados nuevos desde" in note.group(1)
    assert f'<time datetime="{_iso(old)}" data-ut="dt" data-stack>' in note.group(1)
    assert "Las imágenes de SDO/AIA llegan marcadas con problemas de calidad." in note.group(1)
    assert html.index("data-flare-stale") < html.index('class="flare-sec flare-images"')
    html_en = client.get("/en" + PAGE).content.decode()
    assert "No new results since" in html_en and "The SDO/AIA images arrive flagged with quality problems." in html_en


@pytest.mark.django_db
def test_page_notice_without_reasons_when_flare_nrt_gives_none(client, service, public):
    _publish_v2(valid_from=_slot_now() - dt.timedelta(minutes=40))
    _flare_status(service, last_ok=None)
    html = client.get(PAGE).content.decode()
    assert "Sin resultados nuevos desde" in html and "flare-stale-why" not in html


@pytest.mark.django_db
def test_no_notice_while_results_are_fresh_and_no_status_call(client, service, public):
    _publish_v2(valid_from=_slot_now() - dt.timedelta(minutes=10))
    html = client.get(PAGE).content.decode()
    assert "data-flare-stale" not in html
    assert not any(url.endswith("/api/status") for url, _p, _t in service.calls)


@pytest.mark.django_db
def test_no_notice_when_flare_nrt_has_a_newer_result(client, service, public):
    now = _slot_now()
    _publish_v2(valid_from=now - dt.timedelta(minutes=40))
    _flare_status(service, last_ok=now - dt.timedelta(minutes=4))
    assert "data-flare-stale" not in client.get(PAGE).content.decode()


@pytest.mark.django_db
def test_difference_tiles_load_with_the_page(client, service, public):
    _publish_v2()
    html = client.get(PAGE).content.decode()
    tiles = re.findall(r'<img src="[^"]*kind=diff[^"]*"[^>]*>', html)
    assert len(tiles) == 4
    assert all('fetchpriority="low"' in t and "loading=" not in t for t in tiles)
