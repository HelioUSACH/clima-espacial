import datetime as dt

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import get_language
from django.views.decorators.http import require_http_methods

from core.models import Event, Heartbeat, Product, TimeSeriesPoint

from . import charts
from .forms import RegistrationForm

RANGE_DAYS = {"1d": 1, "3d": 3, "7d": 7}

FLARE_PRODUCT_ID = "usach.flare.nrt"


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
    context_product_ids = [
        "noaa.rtsw.mag",
        "noaa.rtsw.wind",
        "noaa.kp",
        "noaa.dst",
        "noaa.goes.xray",
        "noaa.goes.protons",
    ]
    products = {p.id: p for p in Product.objects.filter(id__in=context_product_ids)}
    heartbeats = {h.product_id: h for h in Heartbeat.objects.filter(product_id__in=context_product_ids)}

    charts_ctx = []
    for product_id in context_product_ids:
        product = products.get(product_id)
        if product is None:
            continue  # collector hasn't run yet on a fresh install — degrade, don't break
        hb = heartbeats.get(product_id)
        charts_ctx.append(
            {
                "product": product,
                "title": _fmt_title(product, lang),
                "fields": _chart_fields(product_id, lang),
                "fields_id": _dom_id(product_id) + "-fields",
                "status": _status_badge(product_id, hb.effective_status() if hb else "no_data", lang),
            }
        )

    scales_event = Event.objects.filter(product_id="noaa.scales").order_by("-issued").first()
    scales_badges = _scales_badges(scales_event, lang) if scales_event else []

    return render(
        request,
        "web/home.html",
        {
            "charts": charts_ctx,
            "scales_badges": scales_badges,
            "flare_card_ready": _flare_card_ready(),
        },
    )


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


def _chart_fields(product_id, lang):
    spec = charts.CHART_SPECS.get(product_id)
    if spec:
        return [
            {
                "key": field,
                "label": label_es if lang == "es" else label_en,
                "unit": unit,
                "color": charts.SLOT_COLORS[slot][_mode()],
            }
            for field, label_es, label_en, unit, slot in spec
        ]
    channels = charts.ENERGY_CHANNELS.get(product_id, [])
    return [
        {
            "key": energy,
            "label": label_es if lang == "es" else label_en,
            "unit": "",
            "color": charts.SLOT_COLORS[slot][_mode()],
        }
        for energy, label_es, label_en, slot in channels
    ]


def _mode():
    # Server-rendered pages can't know the client's OS theme; ship the light
    # tokens and let charts.js itself pick dark tokens client-side for the
    # chart chrome. Line colors are close enough in both modes (palette.md)
    # that using the light step server-side is an acceptable M1 simplification.
    return "light"


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
        "default_range": charts.DEFAULT_RANGE,
    }
    if product.kind == "event":
        context["latest_event"] = Event.objects.filter(product=product).order_by("-issued").first()
        return render(request, "web/product_detail_event.html", context)

    context["fields"] = _chart_fields(product_id, lang)
    context["fields_id"] = _dom_id(product_id) + "-fields"
    return render(request, "web/product_detail.html", context)


def product_series_json(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    range_key = request.GET.get("range", charts.DEFAULT_RANGE)
    days = RANGE_DAYS.get(range_key, RANGE_DAYS[charts.DEFAULT_RANGE])
    since = timezone.now() - dt.timedelta(days=days)

    points = (
        TimeSeriesPoint.objects.filter(product=product, time_tag__gte=since)
        .order_by("time_tag")
        .values("time_tag", "payload")
    )

    spec = charts.CHART_SPECS.get(product_id)
    if spec:
        keys = [field for field, *_ in spec]
        time_values, series = _columns(points, keys)
    else:
        channels = [c[0] for c in charts.ENERGY_CHANNELS.get(product_id, [])]
        time_values, series = _channel_columns(points, channels)

    return JsonResponse({"time": time_values, "series": series})


def _columns(points, keys):
    time_values = []
    series = {k: [] for k in keys}
    for row in points:
        time_values.append(int(row["time_tag"].timestamp()))
        for k in keys:
            series[k].append(row["payload"].get(k))
    return time_values, series


def _channel_columns(points, channels):
    # Energy-channel products (X-ray, protons) interleave one row per
    # channel per timestamp; re-pivot into aligned columns on the shared
    # timestamp set so uPlot gets one x-array for every series.
    by_time = {}
    for row in points:
        t = int(row["time_tag"].timestamp())
        by_time.setdefault(t, {})[row["payload"].get("energy")] = row["payload"].get("flux")
    time_values = sorted(by_time)
    series = {c: [by_time[t].get(c) for t in time_values] for c in channels}
    return time_values, series


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
