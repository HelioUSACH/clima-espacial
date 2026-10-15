import datetime as dt

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language, gettext
from django.views.decorators.http import require_http_methods

from core.models import Event, Heartbeat, Product, TimeSeriesPoint

from . import charts
from .forms import RegistrationForm

FLARE_PRODUCT_ID = "usach.flare.nrt"
SCALES_PRODUCT_ID = "noaa.scales"

# 12-HANDOFF §4: the home "now" row — each tile's (product_id, energy filter
# for the two GOES products, which interleave channels on one row instead of
# one column) and unit. Labels are a manual two-language dict (same
# convention as STATUS_LABELS/scale titles above) since these are built
# alongside a dynamically fetched value, not a static template string.
NOW_ROW_LABELS = {
    "bz": {"es": "Bz (IMF)", "en": "Bz (IMF)"},
    "speed": {"es": "Velocidad del viento", "en": "Solar wind speed"},
    "density": {"es": "Densidad", "en": "Density"},
    "kp": {"es": "Kp", "en": "Kp"},
    "xray": {"es": "Rayos X", "en": "X-rays"},
    "protons": {"es": "Protones ≥10 MeV", "en": "≥10 MeV protons"},
}


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


def home(request):
    lang = _lang(request)

    scales_event = Event.objects.filter(product_id="noaa.scales").order_by("-issued").first()
    scales_badges = _scales_badges(scales_event, lang) if scales_event else []

    panels = [
        _panel_json(product_id, panel, lang)
        for product_id in charts.HOME_PRODUCT_ORDER
        for panel in charts.PRODUCT_PANELS[product_id]
    ]

    return render(
        request,
        "web/home.html",
        {
            "now_row": _now_row(lang),
            "panels": panels,
            "chart_i18n": _chart_i18n(),
            "stack_id": "home",
            "panels_id": "home-panels",
            "i18n_id": "home-i18n",
            "ranges": charts.RANGE_CHOICES,
            "default_range": charts.DEFAULT_RANGE_HOME,
            "scales_badges": scales_badges,
            "flare_card_ready": _flare_card_ready(),
        },
    )


def _latest_row(product_id, energy=None):
    qs = TimeSeriesPoint.objects.filter(product_id=product_id)
    if energy is not None:
        qs = qs.filter(payload__energy=energy)
    return qs.order_by("-time_tag").first()


def _stat_tile(key, lang, value, unit, last_seen_at):
    # Pre-formatted here, not with the template's `floatformat` (which
    # returns "" for a non-numeric value like the X-ray class string —
    # found while checking this page against real seeded dev data, not
    # just reasoned about), so every tile's `value` is already the exact
    # display string, no further filtering needed in the template.
    display = f"{value:.1f}" if isinstance(value, (int, float)) else value
    return {"key": key, "label": NOW_ROW_LABELS[key][lang], "value": display, "unit": unit, "last_seen_at": last_seen_at}


def _now_row(lang):
    mag = _latest_row("noaa.rtsw.mag")
    wind = _latest_row("noaa.rtsw.wind")
    kp_row = _latest_row("noaa.kp")
    xray_row = _latest_row("noaa.goes.xray", energy="0.1-0.8nm")
    proton_row = _latest_row("noaa.goes.protons", energy=">=10 MeV")
    return [
        _stat_tile("bz", lang, mag.payload.get("bz_gsm") if mag else None, "nT", mag.time_tag if mag else None),
        _stat_tile(
            "speed", lang, wind.payload.get("proton_speed") if wind else None, "km/s", wind.time_tag if wind else None
        ),
        _stat_tile(
            "density", lang, wind.payload.get("proton_density") if wind else None, "p/cm³", wind.time_tag if wind else None
        ),
        _stat_tile("kp", lang, kp_row.payload.get("kp") if kp_row else None, "", kp_row.time_tag if kp_row else None),
        _stat_tile(
            "xray",
            lang,
            charts.xray_class(xray_row.payload.get("flux")) if xray_row else None,
            "",
            xray_row.time_tag if xray_row else None,
        ),
        _stat_tile(
            "protons", lang, proton_row.payload.get("flux") if proton_row else None, "pfu",
            proton_row.time_tag if proton_row else None,
        ),
    ]


def _panel_json(product_id, panel, lang):
    series = []
    for s in panel.series:
        entry = {"field": s.field, "label": s.label_en if lang == "en" else s.label_es}
        if s.slot is not None:
            entry["color"] = charts.SLOT_COLOR_VARS[s.slot]
        series.append(entry)
    return {
        "product_id": product_id,
        "dom_id": _dom_id(product_id) + "-" + panel.key,
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
    }


def _chart_i18n():
    # One shared blob (web/templates/web/_panel_stack.html) instead of
    # repeating these in every panel's own JSON. gettext() resolves against
    # the request's actual active language, same msgids already in
    # locale/en/LC_MESSAGES/django.po for the pre-panel-stack charts.
    return {
        "time_label": gettext("Hora"),
        "no_data_message": gettext("Sin datos todavía."),
        "error_message": gettext("No se pudo cargar la serie."),
        "trend_up": gettext("en aumento"),
        "trend_down": gettext("en descenso"),
        "trend_flat": gettext("estable"),
        "active_source": gettext("Fuente activa:"),
    }


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
        Product.objects.select_related("heartbeat")
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


def product_detail(request, product_id):
    lang = _lang(request)
    product = get_object_or_404(Product, id=product_id)
    hb = getattr(product, "heartbeat", None)
    context = {
        "product": product,
        "title": _fmt_title(product, lang),
        "status": _status_badge(product_id, hb.effective_status() if hb else "no_data", lang),
        "ranges": charts.RANGE_CHOICES,
        "default_range": charts.DEFAULT_RANGE_PRODUCT,
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
        return render(request, "web/product_detail_event.html", context)

    stack_id = _dom_id(product_id)
    context["panels"] = [_panel_json(product_id, panel, lang) for panel in charts.PRODUCT_PANELS.get(product_id, ())]
    context["chart_i18n"] = _chart_i18n()
    context["stack_id"] = stack_id
    context["panels_id"] = stack_id + "-panels"
    context["i18n_id"] = stack_id + "-i18n"
    context["tall"] = True
    return render(request, "web/product_detail.html", context)


def product_series_json(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    range_key = request.GET.get("range", charts.DEFAULT_RANGE_PRODUCT)
    days = charts.RANGE_DAYS.get(range_key, charts.RANGE_DAYS[charts.DEFAULT_RANGE_PRODUCT])
    since = timezone.now() - dt.timedelta(days=days)

    points = (
        TimeSeriesPoint.objects.filter(product=product, time_tag__gte=since)
        .order_by("time_tag")
        .values("time_tag", "source", "payload")
    )

    fields = charts.product_fields(product_id)
    if product_id in charts.ENERGY_CHANNEL_PRODUCTS:
        time_values, series, source_values = _channel_columns(points, fields)
    else:
        time_values, series, source_values = _columns(points, fields)

    time_values, series, source_values = _insert_gap_markers(time_values, series, source_values)

    payload = {"time": time_values, "series": series}
    # Active-L1-source annotation (12-HANDOFF §4) — only worth sending for
    # products that actually carry a spacecraft name per row (mag, wind);
    # everything else leaves TimeSeriesPoint.source blank.
    if any(source_values):
        payload["source"] = source_values
    return JsonResponse(payload)


def _columns(points, keys):
    time_values = []
    source_values = []
    series = {k: [] for k in keys}
    for row in points:
        time_values.append(int(row["time_tag"].timestamp()))
        source_values.append(row["source"] or None)
        for k in keys:
            series[k].append(row["payload"].get(k))
    return time_values, series, source_values


def _channel_columns(points, channels):
    # Energy-channel products (X-ray, protons) interleave one row per
    # channel per timestamp; re-pivot into aligned columns on the shared
    # timestamp set so uPlot gets one x-array for every series.
    by_time = {}
    source_by_time = {}
    for row in points:
        t = int(row["time_tag"].timestamp())
        by_time.setdefault(t, {})[row["payload"].get("energy")] = row["payload"].get("flux")
        if row["source"]:
            source_by_time[t] = row["source"]
    time_values = sorted(by_time)
    series = {c: [by_time[t].get(c) for t in time_values] for c in channels}
    source_values = [source_by_time.get(t) for t in time_values]
    return time_values, series, source_values


def _insert_gap_markers(time_values, series, source_values):
    # 12-HANDOFF §4: "gaps stay gaps (no interpolation across missing
    # data)". uPlot draws a straight segment between any two consecutive
    # points regardless of their time distance, so a missing collector run
    # would otherwise silently bridge the gap with a line. A single `null`
    # row inserted where a sample is clearly overdue breaks the line there
    # — uPlot skips both segments touching a null point.
    #
    # The threshold comes from the *observed* median spacing in this same
    # result set, not the manifest's `cadence_seconds` (how often the
    # collector polls) — those two differ for noaa.kp, whose collector
    # polls every 15 min for data NOAA itself only updates every 3h,
    # which made every single real point look like it followed a gap
    # (confirmed against real seeded dev data, not just reasoned about).
    if len(time_values) < 3:
        return time_values, series, source_values
    deltas = sorted(time_values[i + 1] - time_values[i] for i in range(len(time_values) - 1))
    median_delta = deltas[len(deltas) // 2]
    threshold = max(median_delta * 2.5, 120)
    new_time, new_source = [], []
    new_series = {k: [] for k in series}
    for i, t in enumerate(time_values):
        if new_time and t - new_time[-1] > threshold:
            new_time.append(new_time[-1] + median_delta)
            new_source.append(None)
            for k in new_series:
                new_series[k].append(None)
        new_time.append(t)
        new_source.append(source_values[i])
        for k in new_series:
            new_series[k].append(series[k][i])
    return new_time, new_series, new_source


def que_ofrecemos(request):
    return render(request, "web/que_ofrecemos.html")


def status(request):
    lang = _lang(request)
    products = Product.objects.select_related("heartbeat").order_by("module", "id")
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
