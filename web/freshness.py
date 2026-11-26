"""The stale-product e-mail (v0.6.4, D-119): `manage.py check_freshness`,
run every 5 minutes by `swx-freshness.timer`.

For each current product, the state `/estado/` shows, in three kinds:
"ok" (al día, al día · con retraso), "stale" (desactualizado, con errores)
and "no_data" (sin datos: never mailed about). The flare product is judged
on its newest **valid result** instead (web/flare.py `newest_result`: the
stored result's slot or flare-nrt's own `last_ok_slot`, 1.3.1), stale after
`flare.RESULT_STALE_AFTER` (30 min): on 2026-11-24 flare-nrt answered every
poll and rejected every frame for 18 h, so its collector's heartbeat stayed
fresh while no result was made.

One e-mail when a product turns stale, one when it is back, to
`SWX_ALERT_EMAILS`; at most one per product per hour (a change inside the
hour waits for the next check, and a spell that ends inside it is never
mailed twice); none on a product's first check (it only records the
state, so a deploy never mails). Without recipients or SMTP nothing is
sent and the change is recorded as told. A failed send is logged and
retried at the next check, never raised.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone, translation

from core.manifests.loader import discover_manifests
from core.models import Heartbeat, Product

from . import charts, flare
from .models import FreshnessAlert
from .product_status import latest_datum, public_status

logger = logging.getLogger(__name__)

OK, STALE, NO_DATA = "ok", "stale", "no_data"
KIND = {"ok": OK, "delayed": OK, "stale": STALE, "error": STALE, "no_data": NO_DATA}
MAIL_INTERVAL = dt.timedelta(hours=1)
SUBJECT_PREFIX = "[Clima Espacial USACH] "
SENT, FAILED, OFF = "sent", "failed", "off"


@dataclasses.dataclass
class Check:
    product_id: str
    title: str
    state: str                       # OK, STALE or NO_DATA
    label: str                       # /estado/'s own words, Spanish
    since: dt.datetime | None = None  # when it turned stale, when known
    last_datum: dt.datetime | None = None
    last_datum_what: str = "dato más reciente"
    notes: list[str] = dataclasses.field(default_factory=list)


def fmt_when(when: dt.datetime | None) -> str:
    return when.astimezone(dt.UTC).strftime("%Y-%m-%d %H:%M UT") if when else "—"


def _label(status: str) -> str:
    return charts.STATUS_LABELS[status]["es"]


def check_product(product: Product, now: dt.datetime) -> Check:
    if product.id == flare.PRODUCT_ID:
        return check_flare(product, now)
    try:
        hb = product.heartbeat
    except Heartbeat.DoesNotExist:
        hb = None
    status, latest = public_status(product, hb.effective_status() if hb else "no_data", now=now)
    check = Check(product.id, product.title_es, KIND.get(status, STALE), _label(status))
    check.last_datum = latest or latest_datum(product)
    if status == "stale" and hb is not None and hb.last_seen_at:
        check.since = hb.last_seen_at + dt.timedelta(seconds=product.stale_after_seconds)
    if status == "error" and hb is not None and hb.detail:
        check.notes.append(f"Detalle del colector: {hb.detail}")
    return check


def check_flare(product: Product, now: dt.datetime) -> Check:
    status_json = flare.service_json("status")
    newest = flare.newest_result(flare._latest_event(), status_json)
    if newest is None:
        check = Check(product.id, product.title_es, NO_DATA, _label("no_data"))
    elif now - newest > flare.RESULT_STALE_AFTER:
        check = Check(product.id, product.title_es, STALE, _label("stale"),
                      since=newest + flare.RESULT_STALE_AFTER)
    else:
        check = Check(product.id, product.title_es, OK, _label("ok"))
    check.last_datum, check.last_datum_what = newest, "resultado válido más reciente (imagen AIA)"
    if status_json is None:
        check.notes.append("flare-nrt no responde (/api/status).")
        return check
    streak = status_json.get("rejected_streak")
    if isinstance(streak, int) and not isinstance(streak, bool) and streak > 0:
        since = flare.parse_time(status_json.get("rejected_since"))
        check.notes.append(f"flare-nrt: {streak} imágenes seguidas sin resultado"
                           + (f", desde {fmt_when(since)}" if since else "") + ".")
    codes = status_json.get("last_reject_reasons")
    if check.state == STALE and isinstance(codes, list) and codes:
        with translation.override("es"):
            phrases = flare.reject_reasons(codes)
        check.notes.append("Motivos de la última imagen rechazada: " + ", ".join(str(c) for c in codes))
        check.notes.extend(f"  → {p}" for p in phrases)
    return check


def status_url() -> str:
    origin = settings.SWX_SITE_ORIGIN or next(iter(settings.CSRF_TRUSTED_ORIGINS), "")
    with translation.override("es"):
        path = reverse("web:status")
    return origin.rstrip("/") + settings.SWX_SCRIPT_NAME + path


def compose(check: Check, kind: str, stale_since: dt.datetime | None) -> tuple[str, str]:
    """(subject, body) of the STALE or recovery (OK) mail."""
    if kind == STALE:
        subject = f"{SUBJECT_PREFIX}Desactualizado: {check.title}"
        lines = [f"Estado: {check.label}", f"Desde: {fmt_when(stale_since)}"]
    else:
        subject = f"{SUBJECT_PREFIX}De nuevo al día: {check.title}"
        lines = [f"Estado: {check.label}", f"Estuvo desactualizado desde: {fmt_when(stale_since)}"]
    body = [f"Producto: {check.title} ({check.product_id})", *lines,
            f"Último dato: {fmt_when(check.last_datum)} ({check.last_datum_what})"]
    if check.notes:
        body += ["", *check.notes]
    body += ["", f"Estado de todos los productos: {status_url()}", "",
             "-- ", "manage.py check_freshness (swx-freshness.timer). Un correo cuando un producto",
             "se desactualiza y otro cuando vuelve; como mucho uno por producto por hora."]
    return subject, "\n".join(body) + "\n"


def mail_off_reason() -> str | None:
    if not settings.SWX_EMAIL_ENABLED:
        return "SMTP is not configured (SWX_EMAIL_HOST)"
    if not settings.SWX_ALERT_EMAILS:
        return "no recipients (SWX_ALERT_EMAILS)"
    return None


def send(subject: str, body: str) -> str:
    """SENT, FAILED (logged) or OFF (nothing tried)."""
    if mail_off_reason():
        return OFF
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, settings.SWX_ALERT_EMAILS, fail_silently=False)
    except Exception:  # SMTP down, auth, timeout (EMAIL_TIMEOUT): retried at the next check
        logger.exception("Could not send the freshness alert %r", subject)
        return FAILED
    return SENT


def current_products():
    return Product.objects.filter(id__in=list(discover_manifests())).select_related("heartbeat").order_by("id")


@dataclasses.dataclass
class Outcome:
    check: Check
    action: str          # "first", "mail", "wait", "" (nothing to tell)
    kind: str = ""       # STALE or OK when action is "mail" or "wait"
    subject: str = ""
    body: str = ""
    result: str = ""     # SENT / FAILED / OFF after a real run


def decide(row: FreshnessAlert | None, check: Check, now: dt.datetime) -> Outcome:
    if row is None:
        return Outcome(check, "first")
    if check.state == NO_DATA or check.state == row.notified:
        return Outcome(check, "")
    if check.state == OK and row.notified == NO_DATA:
        return Outcome(check, "")  # data for the first time: nothing went wrong
    # The spell's start: kept in the row once it began (a recovery mail
    # says when it began); a new spell starts now or when the check says.
    stale_since = row.since if (row.state == STALE or check.state == OK) else None
    if check.state == STALE and stale_since is None:
        stale_since = check.since or now
    subject, body = compose(check, check.state, stale_since)
    if row.last_mail_at and now - row.last_mail_at < MAIL_INTERVAL:
        return Outcome(check, "wait", check.state, subject, body)
    return Outcome(check, "mail", check.state, subject, body)


def run(now: dt.datetime | None = None, *, dry_run: bool = False) -> list[Outcome]:
    now = now or timezone.now()
    rows = {r.product_id: r for r in FreshnessAlert.objects.all()}
    outcomes = []
    for product in current_products():
        check = check_product(product, now)
        row = rows.get(product.id)
        outcome = decide(row, check, now)
        outcomes.append(outcome)
        if dry_run:
            continue
        if row is None:
            FreshnessAlert.objects.create(product_id=product.id, state=check.state, notified=check.state,
                                          since=check.since if check.state == STALE else None, checked_at=now)
            continue
        if check.state == STALE and row.state != STALE and row.notified != STALE:
            row.since = check.since or now  # a new spell (a flap inside the hour keeps the first)
        if outcome.action == "mail":
            outcome.result = send(outcome.subject, outcome.body)
            if outcome.result == SENT:
                row.notified, row.last_mail_at = outcome.kind, now
            elif outcome.result == OFF:
                logger.warning("Freshness alert not e-mailed (%s): %s", mail_off_reason(), outcome.subject)
                row.notified = outcome.kind
        if row.notified == OK and check.state == OK:
            row.since = None
        row.state, row.checked_at = check.state, now
        row.save()
    return outcomes


def send_test() -> str:
    subject = f"{SUBJECT_PREFIX}Correo de prueba de las alertas"
    body = ("Correo de prueba de manage.py check_freshness --test-email.\n\n"
            "Si llegó, las alertas de productos desactualizados llegarán a estas direcciones:\n"
            + "".join(f"  {a}\n" for a in settings.SWX_ALERT_EMAILS)
            + f"\nEstado de todos los productos: {status_url()}\n")
    return send(subject, body)
