import datetime as dt
import json

from django.core.cache import cache
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.utils.html import format_html
from django.utils.translation import get_language, gettext
from django.views.decorators.http import require_http_methods

from core.manifests.loader import discover_manifests, get_manifest
from core.models import Event, Forecast, ForecastScore, Heartbeat, Product, TimeSeriesPoint

from . import charts, forecasts, series
from .forms import RegistrationForm
from .numfmt import fmt_number

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
    }


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
            "pill": gettext("próximamente"), "pill_class": "",
            "title": gettext("Datos abiertos y API"),
            "text": gettext("Series curadas con licencia CC BY 4.0, descargas y acceso HAPI con registro gratuito."),
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
            **_range_buttons(charts.HOME_PRODUCT_ORDER, charts.DEFAULT_RANGE_HOME, lang),
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


def _range_buttons(product_ids, default_range, lang):
    since = _history_since(product_ids)
    history_days = (timezone.now() - since).total_seconds() / 86400 if since else None
    buttons = []
    for i, (key, label_es, label_en, days) in enumerate(charts.RANGE_CHOICES):
        # The shortest range is always offered; so is every range while
        # nothing is stored yet (no history to measure against).
        enabled = i == 0 or history_days is None or history_days >= days * _RANGE_COVERAGE
        buttons.append({
            "key": key,
            "label": label_en if lang == "en" else label_es,
            "enabled": enabled,
            "title": "" if enabled else gettext("Datos disponibles desde %(since)s") % {"since": _fmt_ut(since, lang)},
        })
    enabled_keys = [b["key"] for b in buttons if b["enabled"]]
    if default_range not in enabled_keys:
        # The longest available range that is shorter than the default.
        order = [b["key"] for b in buttons]
        default_range = max(enabled_keys, key=order.index)
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
            "status": _status_badge(p.id, p.heartbeat.effective_status() if hasattr(p, "heartbeat") else "no_data", lang),
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
    return []


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
    hb = getattr(product, "heartbeat", None)
    context = {
        "product": product,
        "title": _fmt_title(product, lang),
        "status": _status_badge(product_id, hb.effective_status() if hb else "no_data", lang),
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
    context.update(_range_buttons((product_id,), charts.DEFAULT_RANGE_PRODUCT, lang))
    context["panels"] = [_panel_json(product_id, panel, lang) for panel in charts.PRODUCT_PANELS.get(product_id, ())]
    context["chart_i18n"] = _chart_i18n()
    context["stack_id"] = stack_id
    context["panels_id"] = stack_id + "-panels"
    context["i18n_id"] = stack_id + "-i18n"
    context["tall"] = True
    context["csv_stub"] = True
    context["stack_aria_label"] = context["title"]
    context["headline_values"] = _headline_values(product_id, lang)
    context["description"] = product.description(lang)
    context["facts"] = _facts_list(product, lang)
    return render(request, "web/product_detail.html", context)


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


def _facts_list(product, lang):
    rows = []
    for key, label_es, label_en in _FACT_ROWS:
        value = product.fact(key, lang)
        if value:
            rows.append({"label": label_en if lang == "en" else label_es, "value": value})
    return rows


def product_series_json(request, product_id):
    """Bucketed mean/min/max per field (D-056, contract in web/series.py),
    cached per (product, range) for SERIES_CACHE_SECONDS (D-057). A cache
    hit touches no database table at all — not even the Product lookup —
    so a burst of visitors clicking "7 días" costs one query per minute."""
    range_key = request.GET.get("range", charts.DEFAULT_RANGE_PRODUCT)
    if range_key not in charts.BUCKET_SECONDS:
        range_key = charts.DEFAULT_RANGE_PRODUCT
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
            "status": _status_badge(p.id, p.heartbeat.effective_status() if hasattr(p, "heartbeat") else "no_data", lang),
            "last_seen_at": p.heartbeat.last_seen_at if hasattr(p, "heartbeat") else None,
        }
        for p in products
    ]
    return render(request, "web/status.html", {"rows": rows, "flare_card_ready": _flare_card_ready()})


def about(request):
    return render(request, "web/about.html")


@require_http_methods(["GET", "POST"])
def data_api(request):
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("web:data_api_thanks")
    else:
        form = RegistrationForm()
    return render(request, "web/data_api.html", {"form": form})


def data_api_thanks(request):
    return render(request, "web/data_api_thanks.html")
