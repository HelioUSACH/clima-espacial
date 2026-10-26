import datetime as dt
import json
import re

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from django.utils.translation import get_language, gettext
from django.views.decorators.http import require_http_methods

from api import export
from api.access import client_ip
from api.emails import quickstart as email_quickstart
from api.emails import send_key_email
from api.hapi import HAPI_VERSION, SPEC_VERSION
from api.keys import issue_key
from core.manifests.loader import discover_manifests, get_manifest
from core.models import Event, Forecast, ForecastScore, Heartbeat, Product, TimeSeriesPoint

from . import charts, forecasts, series
from .forms import RegistrationForm
from .models import RegistrationRequest
from .numfmt import fmt_number, fmt_sci
from .product_status import latest_datum, public_status

FLARE_PRODUCT_ID = "usach.flare.nrt"
SCALES_PRODUCT_ID = "noaa.scales"


def _flare_card_ready():
    """D-036: the beta card shows "en preparación" whenever the flare-nrt
    producer (a separate repository, docs/models/INTEGRATION.md) isn't
    actually publishing — never a hard-coded flag, so launch is never
    blocked on it and it comes up automatically once the service does."""
    try:
        hb = Heartbeat.objects.get(product_id=FLARE_PRODUCT_ID)
    except Heartbeat.DoesNotExist:
        return False
    return hb.effective_status() == "ok"


def _lang(request):
    return "en" if get_language() == "en" else "es"


def _fmt_title(product, lang):
    return product.title(lang)


def _dom_id(product_id):
    """Product ids are dotted (e.g. "noaa.rtsw.mag"); dots are legal in an
    HTML id but awkward for anyone who later queries it with a CSS selector,
    so templates get a dash-safe id instead."""
    return product_id.replace(".", "-")


def _status_badge(product_id, effective_status, lang):
    # Q5: a producer that isn't installed yet (flare-nrt, D-036) reads as
    # "en preparación", never "con errores" — that wording is reserved for
    # *our* collectors actually failing.
    if product_id == FLARE_PRODUCT_ID and effective_status in {"error", "no_data"}:
        effective_status = "pending"
    return {
        "status": effective_status,
        "color": charts.STATUS_COLORS[effective_status],
        "label": charts.STATUS_LABELS[effective_status][lang],
        "title": "",
    }


def _fmt_day(when, lang):
    when = when.astimezone(dt.UTC)
    if lang == "en":
        return f"{when.day:02d} {_MONTH_ABBR[when.month - 1]}"
    return f"{when:%d/%m}"


def _product_badge(product, lang):
    """The status pill of one product: its heartbeat, refined to "al día ·
    con retraso" when its newest datum is older than `delayed_after`
    (D-085), with "datos hasta dd/mm" as the tooltip."""
    hb = getattr(product, "heartbeat", None)
    status, latest = public_status(product, hb.effective_status() if hb else "no_data")
    badge = _status_badge(product.id, status, lang)
    badge["latest"] = latest
    if status == "delayed":
        badge["title"] = gettext("datos hasta %(day)s") % {"day": _fmt_day(latest, lang)}
    return badge


def _latest_row(product_id, energy=None):
    qs = TimeSeriesPoint.objects.filter(product_id=product_id)
    if energy is not None:
        qs = qs.filter(payload__energy=energy)
    return qs.order_by("-time_tag").first()


# 18-HANDOFF-v0.2.0.md §2: the "Lo que ofrece USACH" hero sidebar. Maturity
# pills come from the Product registry where a Product row already exists
# (so far only the flare detector, once its collector has run once);
# everything else is the mockup's own static copy — dB/dt has no product
# yet (it's a future model) and the data/API offer is never a product.
def _offer_cards(lang):
    flare = Product.objects.filter(id=FLARE_PRODUCT_ID).first()
    flare_pill = flare.get_maturity_display() if flare else gettext("beta")
    return [
        {
            "pill": flare_pill, "pill_class": "beta",
            "title": gettext("Detección de fulguraciones (NRT)"),
            "text": gettext("Detección automática de fulguraciones solares en tiempo casi real a partir del flujo de rayos X de GOES."),
        },
        {
            "pill": gettext("en desarrollo"), "pill_class": "",
            "title": gettext("dB/dt global"),
            "text": gettext("Pronóstico de variaciones rápidas del campo geomagnético, el insumo de las corrientes inducidas en redes eléctricas."),
        },
        {
            "pill": gettext("disponible"), "pill_class": "",
            "title": gettext("Datos abiertos y API"),
            "text": gettext("Descargas CSV y un servidor HAPI, con registro gratuito. Cada serie conserva su licencia: "
                            "los productos USACH son CC BY 4.0 y los datos de la NOAA, de dominio público."),
            "url": reverse("web:data_api"),
        },
    ]


def home(request):
    lang = _lang(request)
    panels = [
        _panel_json(product_id, panel, lang)
        for product_id in charts.HOME_PRODUCT_ORDER
        for panel in charts.PRODUCT_PANELS[product_id]
    ]
    return render(
        request,
        "web/home.html",
        {
            "offer_cards": _offer_cards(lang),
            "panels": panels,
            "chart_i18n": _chart_i18n(),
            "stack_id": "home",
            "panels_id": "home-panels",
            "i18n_id": "home-i18n",
            "stack_aria_label": gettext("Viento solar en L1"),
            **_range_buttons(charts.HOME_PRODUCT_ORDER, charts.DEFAULT_CHART.ranges, charts.DEFAULT_RANGE_HOME, lang),
        },
    )


def _panel_json(product_id, panel, lang):
    series = []
    for s in panel.series:
        entry = {"field": s.field, "label": s.label_en if lang == "en" else s.label_es}
        if s.slot is not None:
            entry["color"] = charts.SLOT_COLOR_VARS[s.slot]
        series.append(entry)
    return {
        "product_id": product_id,
        "endpoint": reverse("web:product_series_json", args=[product_id]),
        "key": panel.key,
        "title": panel.title_en if lang == "en" else panel.title_es,
        "unit": panel.unit,
        "kind": panel.kind,
        "y": panel.y,
        "series": series,
        "zero_line": panel.zero_line,
        "ref_lines": [{"value": v, "label": label} for v, label in panel.ref_lines],
        "log_bands": [{"value": v, "label": label} for v, label in panel.log_bands],
        "show_source": panel.show_source,
        "kp_bucket": panel.kp_bucket,
        "envelope": panel.envelope,
        "y_range": list(panel.y_range) if panel.y_range else None,
        "anchor": panel.anchor,
        # D-097: stack-wide, repeated per panel (a stack is one product
        # page, or home's default-chart pair).
        "caption": charts.product_chart(product_id).caption(lang),
        "daily": charts.product_chart(product_id).daily,
    }


def _chart_i18n():
    # One shared blob (web/templates/web/_panel_stack.html) instead of
    # repeating these in every panel's own JSON. gettext() resolves against
    # the request's actual active language, same msgids already in
    # locale/en/LC_MESSAGES/django.po for the pre-ECharts charts.
    return {
        "lang": "en" if get_language() == "en" else "es",
        "time_axis_label": gettext("UT"),
        # Not run through gettext(): they're already the English text an EN
        # page needs (an ES page never reads this list) -- gettext() here
        # picked up Django's own bundled Spanish translation of "May" as a
        # common word and silently turned it into "mayo" on ES pages.
        "months": list(_MONTH_ABBR),
        "no_data_message": gettext("Sin datos todavía."),
        "error_message": gettext("No se pudo cargar la serie."),
        "trend_up": gettext("en aumento"),
        "trend_down": gettext("en descenso"),
        "trend_flat": gettext("estable"),
        "active_source_label": gettext("Nave activa"),
        "last_datum_label": gettext("Último dato"),
        "data_until_label": gettext("datos hasta"),
        "delayed_suffix": gettext("(con retraso)"),
        "source_mark_note": gettext("Línea discontinua: cambio de nave activa"),
        "averages_label": gettext("promedios de"),
        "day_label": gettext("día"),
        "minutes_unit": gettext("min"),
        "seconds_unit": gettext("s"),
    }


# English month abbreviations for the EN time axis ("15 Oct"); ES uses
# dd/mm and never reads these. Translated through gettext anyway so the
# list isn't English-only by accident if a third language ever appears.
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# A range button is offered once the stored history covers most of it
# (13-AUDIT §3: "7 días" was offered with ~1.5 days of data). 0.9, not
# 1.0, so a 7-day archive that's a few minutes short still gets "7 días".
_RANGE_COVERAGE = 0.9
_HISTORY_CACHE_SECONDS = 300


def _fmt_ut(when, lang):
    when = when.astimezone(dt.UTC)
    if lang == "en":
        return f"{when.day:02d} {gettext(_MONTH_ABBR[when.month - 1])} {when:%H:%M} UT"
    return f"{when:%d/%m %H:%M} UT"


def _fmt_day(when, lang):
    when = when.astimezone(dt.UTC)
    if lang == "en":
        return f"{when.day:02d} {gettext(_MONTH_ABBR[when.month - 1])}"
    return f"{when:%d/%m}"


def _history_since(product_ids):
    """The newest of the products' oldest stored points: a range is only
    complete once *every* panel in the stack has data that far back. A
    product with nothing stored yet doesn't count (its panel shows its own
    empty state either way)."""
    cache_key = "swx:history:" + ",".join(product_ids)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None
    oldest = []
    for pid in product_ids:
        first = TimeSeriesPoint.objects.filter(product_id=pid).order_by("time_tag").values_list("time_tag", flat=True)
        first = first.first()
        if first is not None:
            oldest.append(first)
    since = max(oldest) if oldest else None
    cache.set(cache_key, since or "", _HISTORY_CACHE_SECONDS)
    return since


def _range_buttons(product_ids, range_keys, default_range, lang):
    since = _history_since(product_ids)
    history_days = (timezone.now() - since).total_seconds() / 86400 if since else None
    buttons = []
    choices = [c for c in charts.RANGE_CHOICES if c[0] in range_keys]
    for i, (key, label_es, label_en, days) in enumerate(choices):
        # The shortest range is always offered; so is every range while
        # nothing is stored yet (no history to measure against), and
        # "todo", whatever is stored.
        enabled = i == 0 or days is None or history_days is None or history_days >= days * _RANGE_COVERAGE
        buttons.append({
            "key": key,
            "label": label_en if lang == "en" else label_es,
            "enabled": enabled,
            "title": "" if enabled else gettext("Datos disponibles desde %(since)s") % {"since": _fmt_ut(since, lang)},
        })
    enabled_keys = [b["key"] for b in buttons if b["enabled"]]
    if default_range not in enabled_keys:
        # The longest available range that is shorter than the default
        # ("todo", always available, is longer).
        order = [b["key"] for b in buttons]
        shorter = [k for k in enabled_keys if order.index(k) < order.index(default_range)]
        default_range = shorter[-1] if shorter else enabled_keys[0]
    return {"ranges": buttons, "default_range": default_range}


def _license_text(raw):
    # The manifests' `license:` is English data ("Public domain (NOAA)");
    # translate the known value on the page (13-AUDIT §3), fall back to
    # the raw text for anything else.
    if raw == "Public domain (NOAA)":
        return gettext("Dominio público (NOAA)")
    return raw


def _scales_badges(event, lang):
    today = event.payload.get("days", {}).get("0", {})
    badges = []
    for scale_key, title in (("R", {"es": "Radio (R)", "en": "Radio (R)"}),
                              ("S", {"es": "Radiación (S)", "en": "Radiation (S)"}),
                              ("G", {"es": "Geomagnética (G)", "en": "Geomagnetic (G)"})):
        entry = today.get(scale_key, {})
        level_str = entry.get("Scale")
        level = int(level_str) if level_str not in (None, "") else 0
        role = charts.SCALE_STATUS.get(level, "ok")
        badges.append(
            {
                "scale": scale_key,
                "title": title[lang],
                "level": level,
                "color": charts.SCALE_COLORS[role],
                "text": charts.scale_text(entry.get("Text"), lang),
            }
        )
    return badges


def catalogue(request):
    lang = _lang(request)
    # usach.flare.nrt gets its own hardcoded offer card above (same reason
    # home() never puts it in context_product_ids) — once a real Product
    # row exists for it (as soon as modules/flare's collector runs once),
    # the generic grid below would otherwise show it a second time.
    products = (
        _current_products().select_related("heartbeat")
        .exclude(id=FLARE_PRODUCT_ID)
        .order_by("module", "id")
    )
    rows = [
        {
            "product": p,
            "title": _fmt_title(p, lang),
            "status": _product_badge(p, lang),
        }
        for p in products
    ]
    return render(request, "web/catalogue.html", {"rows": rows, "flare_card_ready": _flare_card_ready()})


def _headline_value(label_es, label_en, value, unit, lang):
    # 19-HANDOFF-v0.2.1.md §1.4: the same ES-comma/EN-point, true-minus
    # formatting as the strip and the chart readout (web/numfmt.py) -- a
    # raw f"{value:.1f}" here read "-120" next to the chart's own "−120".
    display = fmt_number(value, lang, decimals=1) if isinstance(value, (int, float)) else value
    return {"label": label_en if lang == "en" else label_es, "value": display, "unit": unit}


# 18-HANDOFF-v0.2.0.md §2: the product page's headline values -- the
# product's own key field(s), latest datum. Per-product because the right
# field (and how to format it) genuinely differs (Bz vs. a bare index vs.
# a derived X-ray class), not something to generalise from the panel specs.
def _headline_values(product_id, lang):
    if product_id == "noaa.rtsw.mag":
        row = _latest_row(product_id)
        payload = row.payload if row else {}
        return [
            _headline_value("Bz (GSM)", "Bz (GSM)", payload.get("bz_gsm"), "nT", lang),
            _headline_value("Bt", "Bt", payload.get("bt"), "nT", lang),
        ]
    if product_id == "noaa.rtsw.wind":
        row = _latest_row(product_id)
        payload = row.payload if row else {}
        return [
            _headline_value("Velocidad", "Speed", payload.get("proton_speed"), "km/s", lang),
            _headline_value("Densidad", "Density", payload.get("proton_density"), "p/cm³", lang),
        ]
    if product_id == "noaa.kp":
        row = _latest_row(product_id)
        return [_headline_value("Kp", "Kp", row.payload.get("kp") if row else None, "", lang)]
    if product_id == "kyoto.dst":
        row = _latest_row(product_id)
        return [_headline_value("Dst", "Dst", row.payload.get("dst") if row else None, "nT", lang)]
    if product_id == "kyoto.ae":
        row = _latest_row(product_id)
        payload = row.payload if row else {}
        return [_headline_value("AE", "AE", payload.get("ae"), "nT", lang),
                _headline_value("AL", "AL", payload.get("al"), "nT", lang)]
    if product_id == "noaa.goes.xray":
        row = _latest_row(product_id, energy="0.1-0.8nm")
        value = charts.xray_class(row.payload.get("flux")) if row else None
        return [_headline_value("Clase", "Class", value, "", lang)]
    if product_id == "noaa.goes.protons":
        row = _latest_row(product_id, energy=">=10 MeV")
        return [_headline_value("≥10 MeV", "≥10 MeV", row.payload.get("flux") if row else None, "pfu", lang)]
    if product_id == "noaa.goes.electrons":
        row = _latest_row(product_id, energy=">=2 MeV")
        return [_headline_value("≥2 MeV", "≥2 MeV", row.payload.get("flux") if row else None, "1/(cm² s sr)", lang)]
    if product_id == "noaa.goes.electron_fluence":
        row = _latest_row(product_id)
        return [_headline_value("Fluencia, último día", "Fluence, last day",
                                fmt_sci(row.payload.get("fluence") if row else None, lang), "1/(cm² sr)", lang)]
    return []


def _headline_asof(product, badge, lang):
    """"al 04/10 23:55 UT" under the headline values when they aren't
    "now": the product is delayed (D-085) or its chart ends at its latest
    datum (D-082). Otherwise "" -- a live product's values are current.
    A daily product always says which day its value is: "día 21/10" (D-097)."""
    daily = charts.product_chart(product.id).daily
    if not daily and badge["status"] != "delayed" and charts.product_anchor(product.id) != "latest":
        return ""
    latest = badge.get("latest") or latest_datum(product)
    if latest is None:
        return ""
    if daily:
        return f"{gettext('día')} {_fmt_day(latest, lang)}"
    return gettext("al %(when)s") % {"when": _fmt_ut(latest, lang)}


# D-081: a retired product's page moves to its successor for good. Its
# rows stay in the store (history) and its serie.json still answers.
RETIRED_PRODUCTS = {"noaa.dst": "kyoto.dst"}


def _current_products():
    # A product whose manifest is gone is retired: kept in the database
    # with its history, but no longer listed (catalogue, status).
    return Product.objects.filter(id__in=list(discover_manifests()))


def product_detail(request, product_id):
    if product_id in RETIRED_PRODUCTS:
        return redirect("web:product_detail", RETIRED_PRODUCTS[product_id], permanent=True)
    lang = _lang(request)
    product = get_object_or_404(Product, id=product_id)
    context = {
        "product": product,
        "title": _fmt_title(product, lang),
        "status": _product_badge(product, lang),
        "source_license": _license_text(product.source_license),
        "source_links": product.source_links,
    }
    if product.kind == "event":
        event = Event.objects.filter(product=product).order_by("-issued").first()
        context["latest_event"] = event
        # 12-HANDOFF item 4: this used to dump the raw event payload dict
        # (internal field names, English NOAA text) straight onto a public
        # page. noaa.scales reuses the same translated badge rendering as
        # the home page instead; any other kind=event product (e.g. the
        # flare detector) just gets the "emitted at" line above — never the
        # raw payload — until it has its own designed view.
        if event and product_id == SCALES_PRODUCT_ID:
            context["scales_badges"] = _scales_badges(event, lang)
        # D-078: an event product's second link is its official alerts page.
        if len(product.source_links) > 1:
            context["official_alerts"] = product.source_links[1]
        return render(request, "web/product_detail_event.html", context)
    if product.kind == "forecast":
        context.update(_forecast_context(product, lang))
        return render(request, "web/product_detail_forecast.html", context)

    stack_id = _dom_id(product_id)
    chart = charts.product_chart(product_id)
    context.update(_range_buttons((product_id,), chart.ranges, chart.default_range, lang))
    context["panels"] = [_panel_json(product_id, panel, lang) for panel in charts.PRODUCT_PANELS.get(product_id, ())]
    context["chart_i18n"] = _chart_i18n()
    context["stack_id"] = stack_id
    context["panels_id"] = stack_id + "-panels"
    context["i18n_id"] = stack_id + "-i18n"
    context["tall"] = True
    context.update(_download_context(product))
    context["stack_aria_label"] = context["title"]
    context["headline_values"] = _headline_values(product_id, lang)
    context["headline_asof"] = _headline_asof(product, context["status"], lang)
    context["description"] = product.description(lang)
    context["facts"] = _facts_list(product, lang)
    return render(request, "web/product_detail.html", context)


def _download_context(product):
    """The chart card's CSV button (§3): a download dialog for an exportable
    product; otherwise a link to the data on the provider's own site."""
    if export.get_dataset(product.id) is not None:
        return {"csv_download": {"endpoint": reverse("api:download_csv", args=[product.id]),
                                 "product_id": product.id}}
    if product.source_links:
        return {"provider_data_link": product.source_links[0]}
    return {}


# dt/dd order fixed, same for every product (18-HANDOFF §2 "Ficha del
# producto"); a product whose manifest doesn't set a given fact (yet) just
# skips that row instead of showing an empty one.
_FACT_ROWS = (
    ("source", "Fuente", "Source"),
    ("spacecraft", "Naves", "Spacecraft"),
    ("cadence", "Cadencia original", "Original cadence"),
    ("coordinates", "Coordenadas", "Coordinates"),
    ("license", "Licencia", "Licence"),
    ("citation", "Cita", "Citation"),
    ("used_by", "Lo usan", "Used by"),
)


# A DOI, written "doi:10.xxxx/..." or bare "10.xxxx/...": the registrant
# code is 4-9 digits, the suffix runs to the next space. A trailing
# full stop, comma, semicolon or closing bracket is punctuation, not DOI.
_DOI_RE = re.compile(r"(?:\bdoi:\s*)?\b(10\.\d{4,9}/\S+?)(?=[.,;)\]]*(?:\s|$))", re.IGNORECASE)


def link_dois(text):
    """Escaped `text` with every DOI turned into an https://doi.org/ link
    (D-085), keeping the text as written ("doi:10.17593/..."). Safe HTML."""
    parts, last = [], 0
    for match in _DOI_RE.finditer(text):
        parts.append(escape(text[last:match.start()]))
        parts.append(format_html('<a href="https://doi.org/{}" rel="noopener">{}</a>', match.group(1), match.group(0)))
        last = match.end()
    parts.append(escape(text[last:]))
    return mark_safe("".join(parts))  # every piece is escaped or built by format_html


def _facts_list(product, lang):
    rows = []
    for key, label_es, label_en in _FACT_ROWS:
        value = product.fact(key, lang)
        if value:
            rows.append({"label": label_en if lang == "en" else label_es, "value": link_dois(value)})
    return rows


def product_series_json(request, product_id):
    """Bucketed mean/min/max per field (D-056, contract in web/series.py),
    cached per (product, range) for SERIES_CACHE_SECONDS (D-057). A cache
    hit touches no database table at all — not even the Product lookup —
    so a burst of visitors clicking "7 días" costs one query per minute."""
    # Only the product's own ranges (D-097): "1y" of a 1-min series is
    # not something a button offers, so not something a URL can ask for.
    chart = charts.product_chart(product_id)
    range_key = request.GET.get("range", chart.default_range)
    if range_key not in chart.ranges:
        range_key = chart.default_range
    cache_key = f"swx:serie:v1:{product_id}:{range_key}"
    body = cache.get(cache_key)
    if body is None:
        get_object_or_404(Product, id=product_id)
        body = json.dumps(series.bucketed_series(product_id, range_key), separators=(",", ":"))
        cache.set(cache_key, body, charts.SERIES_CACHE_SECONDS)
    response = HttpResponse(body, content_type="application/json")
    patch_cache_control(response, public=True, max_age=charts.SERIES_CACHE_SECONDS)
    return response


def product_forecast_json(request, product_id):
    """One issue against the observed target, or one lead's verification
    pairs (contract in web/forecasts.py). Cached per full query for
    SERIES_CACHE_SECONDS, server and browser, like serie.json (D-057)."""
    params = {k: request.GET.get(k, "") for k in ("issued", "lead", "range", "site")}
    cache_key = "swx:forecast:v1:" + product_id + ":" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    body = cache.get(cache_key)
    if body is None:
        get_object_or_404(Product, id=product_id, kind="forecast")
        try:
            if params["lead"]:
                data = forecasts.lead_view(product_id, lead=params["lead"], site=params["site"],
                                           range_key=params["range"] or forecasts.DEFAULT_LEAD_RANGE)
            else:
                data = forecasts.issue_view(product_id, issued=params["issued"] or "latest", site=params["site"],
                                            range_key=params["range"] or forecasts.DEFAULT_ISSUE_RANGE)
        except forecasts.BadRequest as exc:
            return HttpResponseBadRequest(str(exc), content_type="text/plain; charset=utf-8")
        body = json.dumps(data, separators=(",", ":"))
        cache.set(cache_key, body, charts.SERIES_CACHE_SECONDS)
    response = HttpResponse(body, content_type="application/json")
    patch_cache_control(response, public=True, max_age=charts.SERIES_CACHE_SECONDS)
    return response


def _hours_label(seconds, lang):
    hours = seconds / 3600
    return (fmt_number(hours, lang, decimals=0) if hours == int(hours) else fmt_number(hours, lang)) + " h"


def _score_rows(product, lang):
    # "Desempeño / Performance" (v0.4.0 §6): lead x window, every number
    # through the shared formatter (D-065). Only the newest model version.
    scores = list(ForecastScore.objects.filter(product=product).order_by("-computed_at", "lead_seconds", "window_days"))
    if not scores:
        return None, []
    version = max(scores, key=lambda s: s.computed_at).model_version
    rows = []
    for sc in sorted((s for s in scores if s.model_version == version), key=lambda s: (s.lead_seconds, s.window_days)):
        coverage = None
        if sc.coverage is not None:
            nominal = f" / {fmt_number(sc.nominal_coverage * 100, lang, decimals=0)} %" if sc.nominal_coverage else ""
            coverage = f"{fmt_number(sc.coverage * 100, lang, decimals=0)} %{nominal}"
        rows.append({
            "lead": _hours_label(sc.lead_seconds, lang),
            "window": f"{sc.window_days} " + (gettext("días") if sc.window_days != 1 else gettext("día")),
            "n": sc.n,
            "mae": fmt_number(sc.mae, lang, decimals=2),
            "rmse": fmt_number(sc.rmse, lang, decimals=2),
            "bias": fmt_number(sc.bias, lang, decimals=2),
            "skill": fmt_number(sc.skill, lang, decimals=2),
            "coverage": coverage,
        })
    return version, rows


def _forecast_context(product, lang):
    target_id, panel = forecasts.target_panel(product.id)
    target = get_manifest(target_id)
    spec = get_manifest(product.id)["forecast"]
    field = spec["target"]["field"]
    param = next((p for p in target.get("parameters", []) if p["name"] == field), {})
    uncertainty = spec["uncertainty"]
    level = uncertainty.get("level")
    stack_id = _dom_id(product.id)
    version, score_rows = _score_rows(product, lang)
    config = {
        "endpoint": reverse("web:product_forecast_json", args=[product.id]),
        "field": field,
        "unit": param.get("units", "") if param.get("units", "") != "1" else "",
        "y_range": param.get("range"),
        "bars": bool(panel and panel.kind == "bars"),
        "kp_bucket": bool(panel and panel.kp_bucket),
        "ref_lines": [{"value": v, "label": label} for v, label in (panel.ref_lines if panel else ())],
        "title": (panel.title_en if lang == "en" else panel.title_es) if panel else field,
        "bounds": uncertainty["type"] == "bounds",
    }
    i18n = dict(
        _chart_i18n(),
        observed=gettext("Observado"),
        forecast=gettext("Pronóstico"),
        band=(gettext("Intervalo %(pct)s %%") % {"pct": fmt_number(level * 100, lang, decimals=0)}) if level else "",
        issued=gettext("emitido"),
        data_until=gettext("datos hasta"),
        now=gettext("ahora"),
        lead_label=gettext("anticipación"),
        valid_label=gettext("válido"),
        no_issue=gettext("Todavía no hay pronósticos emitidos."),
        band_note=(gettext("Banda: intervalo central de %(pct)s %%") % {"pct": fmt_number(level * 100, lang, decimals=0)}) if level else "",
        future_note=gettext("zona sombreada: después de ahora"),
    )
    return {
        "forecast_config": config,
        "forecast_i18n": i18n,
        "stack_id": stack_id,
        "config_id": stack_id + "-config",
        "i18n_id": stack_id + "-i18n",
        "issue_ranges": [{"key": key, "label": label_en if lang == "en" else label_es}
                         for key, label_es, label_en, _days in charts.RANGE_CHOICES if key in forecasts.ISSUE_RANGES],
        "lead_ranges": [{"key": "7d", "label": "7 " + gettext("días")}, {"key": "30d", "label": "30 " + gettext("días")}],
        "leads": [{"key": key, "label": _hours_label(seconds, lang)} for key, seconds in forecasts.horizons(product.id)],
        "default_issue_range": forecasts.DEFAULT_ISSUE_RANGE,
        "score_version": version,
        "score_rows": score_rows,
        "target_title": target["title"]["en" if lang == "en" else "es"],
        "target_provider_html": _provider_link(target["source"]),
        "description": product.description(lang),
        "facts": _facts_list(product, lang),
        "headline_values": _forecast_headline(product, field, lang),
    }


def _provider_link(source):
    """A manifest `source`'s provider, linked to its first `links` entry
    (D-078) when it has one. Safe HTML, so a blocktrans keeps it as is."""
    links = source.get("links") or []
    if not links:
        return format_html("{}", source["provider"])
    return format_html('<a href="{}" rel="noopener nofollow">{} {}</a>', links[0]["url"], source["provider"],
                       render_to_string("web/_icon_ext.html"))


def _forecast_headline(product, field, lang):
    # The latest issue's shortest lead, e.g. "Kp en 3 h".
    row = (Forecast.objects.filter(product=product).order_by("-issued_at", "lead_seconds")
           .values_list("lead_seconds", "values").first())
    if row is None:
        return [_headline_value(f"{field} +3 h", f"{field} +3 h", None, "", lang)]
    lead, values = row
    hours = _hours_label(lead, lang)
    label = field.capitalize() if len(field) <= 3 else field
    return [_headline_value(f"{label} en {hours}", f"{label} in {hours}", (values.get(field) or {}).get("value"), "", lang)]


def que_ofrecemos(request):
    return render(request, "web/que_ofrecemos.html")


def status(request):
    lang = _lang(request)
    products = _current_products().select_related("heartbeat").order_by("module", "id")
    rows = [
        {
            "product": p,
            "title": _fmt_title(p, lang),
            "status": _product_badge(p, lang),
            "last_seen_at": p.heartbeat.last_seen_at if hasattr(p, "heartbeat") else None,
        }
        for p in products
    ]
    return render(request, "web/status.html", {"rows": rows, "flare_card_ready": _flare_card_ready()})


def about(request):
    return render(request, "web/about.html")


# --- Data access (D-083, D-086) ------------------------------------------------

DUPLICATE_WINDOW = dt.timedelta(minutes=10)


def _dataset_rows(lang):
    rows = []
    for ds in export.datasets():
        rows.append({
            "id": ds.id,
            "title": ds.title["en" if lang == "en" else "es"],
            "cadence": ds.fact("cadence", lang) or ds.manifest["cadence"],
            "license": ds.fact("license", lang) or _license_text(ds.license),
            "relay": ds.relay,
            "provider": ds.provider,
            "provider_url": ds.provider_url,
            # HAPI's catalog lists a dataset once it has data (D-088), so
            # only then does its id link to `info` (D-093).
            "hapi_listed": export.time_bounds(ds)[0] is not None,
        })
    return rows


def _not_exported_rows(lang):
    return [{"id": m["id"], "title": m["title"]["en" if lang == "en" else "es"],
             "license": (m.get("facts", {}).get("license") or {}).get(lang) or m["source"].get("license", ""),
             "links": m["source"].get("links", [])} for m in export.not_exported()]


def _forecast_rows(lang):
    return [{"id": pid, "title": m["title"]["en" if lang == "en" else "es"],
             "license": (m.get("facts", {}).get("license") or {}).get(lang) or m["source"].get("license", "")}
            for pid, m in sorted(discover_manifests().items()) if m["kind"] == "forecast"]


def _data_api_context(request, lang, form):
    return {
        "form": form,
        "datasets": _dataset_rows(lang),
        "not_exported": _not_exported_rows(lang),
        "forecasts": _forecast_rows(lang),
        "examples": email_quickstart(request, "TU_LLAVE" if lang == "es" else "YOUR_KEY"),
        "limits": {
            "per_minute": settings.SWX_API_RATE_PER_MINUTE,
            "per_day": settings.SWX_API_DAILY_REQUESTS,
            "rows_per_day": settings.SWX_API_DAILY_ROWS,
            "keyless_hours": settings.SWX_API_KEYLESS_MAX_HOURS,
            "keyless_days": settings.SWX_API_KEYLESS_RECENT_DAYS,
            "keyless_per_hour": settings.SWX_API_KEYLESS_PER_HOUR,
            "max_days_fast": settings.SWX_EXPORT_MAX_DAYS_FAST,
            "max_days_slow": settings.SWX_EXPORT_MAX_DAYS_SLOW,
            "max_rows": settings.SWX_EXPORT_MAX_ROWS,
            "forecast_days": settings.SWX_FORECAST_MAX_DAYS,
        },
        "hapi_url": request.build_absolute_uri(reverse("hapi:landing")),
        "hapi_version": HAPI_VERSION,
        "hapi_spec": SPEC_VERSION,
        "email_enabled": settings.SWX_EMAIL_ENABLED,
    }


def _registrations_today(request):
    return f"swx:reg:{client_ip(request)}:{timezone.now().astimezone(dt.UTC):%Y%m%d}"


@require_http_methods(["GET", "POST"])
def data_api(request):
    """The data-access page, and registration. A valid POST creates the
    registration and a key and answers with the thanks page directly: the
    key is in this one response only (no redirect, no session), which is
    never cached (D-086)."""
    lang = _lang(request)
    status = 200
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if request.POST.get("website"):
            form.add_error(None, gettext("No pudimos procesar el registro. Inténtalo de nuevo."))
            status = 400
        elif form.is_valid():
            email = form.cleaned_data["email"]
            if RegistrationRequest.objects.filter(email__iexact=email,
                                                  created_at__gte=timezone.now() - DUPLICATE_WINDOW).exists():
                response = render(request, "web/data_api_thanks.html", {"duplicate": True})
                response["Cache-Control"] = "no-store"
                return response
            if (cache.get(_registrations_today(request)) or 0) >= settings.SWX_REGISTRATIONS_PER_IP_DAY:
                form.add_error(None, gettext("Demasiados registros desde esta conexión hoy. Inténtalo mañana o "
                                             "escríbenos."))
                status = 429
            else:
                with transaction.atomic():
                    registration = form.save(commit=False)
                    registration.language = lang
                    registration.save()
                    _key, raw = issue_key(registration=registration, kind="user")
                if not cache.add(_registrations_today(request), 1, 86400):
                    cache.incr(_registrations_today(request))
                email_status = send_key_email(request, to=registration.email, name=registration.name, key=raw,
                                              lang=lang)
                response = render(request, "web/data_api_thanks.html", {
                    "key": raw, "qs": email_quickstart(request, raw), "email_status": email_status,
                    "email": registration.email,
                })
                response["Cache-Control"] = "no-store"
                return response
    else:
        form = RegistrationForm()
    return render(request, "web/data_api.html", _data_api_context(request, lang, form), status=status)
