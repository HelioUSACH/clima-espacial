"""`forecast.json`'s data (v0.4.0 §5): one issue against the observed target,
or one lead's forecast/observation pairs for verification. Same caching as
`serie.json` (D-057, in the view).

Issue view, `?issued=latest|<iso>&range=1d|3d|7d`:

    {"view": "issue", "product": id, "model_version": "0.1.0",
     "issued_at": t,                       # UNIX seconds, UTC; null if no issue yet
     "field": "kp", "units": "1",
     "uncertainty": {"type": "bounds", "level": 0.8},
     "rows": {"valid_at": [...], "lead_seconds": [...], "value": [...], "lower": [...], "upper": [...]},
     "observed": <serie.json's contract (D-056) for the target, over `range` before issued_at>}

Verification view, `?lead=3h&range=7d|30d`:

    {"view": "lead", "product": id, "model_version": ..., "lead_seconds": 10800,
     "field": ..., "units": ..., "uncertainty": ...,
     "pairs": {"valid_at": [...], "forecast": [...], "lower": [...], "upper": [...],
               "observed": [...]}}      # observed is null where nothing verifies (yet)

Both take `site=` (default "", a global index). Columnar lists, like
`serie.json`, so the chart maps them straight onto series.
"""

from __future__ import annotations

import datetime as dt

from django.utils import timezone

from core import forecasting, swxkit
from core.manifests.loader import get_manifest, parse_duration
from core.models import Forecast

from . import charts, series

ISSUE_RANGES = ("1d", "3d", "7d")
LEAD_RANGES = {"7d": 7, "30d": 30}
DEFAULT_ISSUE_RANGE = "3d"
DEFAULT_LEAD_RANGE = "7d"


class BadRequest(ValueError):
    """A query parameter that can't be served (the view answers 400)."""


def _epoch(when: dt.datetime | None) -> int | None:
    return int(when.timestamp()) if when else None


def _spec(product_id):
    manifest = get_manifest(product_id)
    spec = manifest["forecast"]
    target = get_manifest(spec["target"]["product"])
    units = next((p.get("units", "") for p in target.get("parameters", []) if p["name"] == spec["target"]["field"]), "")
    return spec, units


def _header(product_id, spec, units, model_version):
    return {"product": product_id, "model_version": model_version, "field": spec["target"]["field"],
            "units": units, "uncertainty": spec["uncertainty"]}


def _parse_issued(raw: str) -> dt.datetime | None:
    if raw in ("", "latest"):
        return None
    try:
        when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise BadRequest(f"issued must be 'latest' or an ISO 8601 time, got {raw!r}") from None
    if timezone.is_naive(when):
        raise BadRequest("issued needs a UTC offset (e.g. 2026-10-17T18:00:00Z)")
    return when


def issue_view(product_id: str, *, issued: str = "latest", range_key: str = DEFAULT_ISSUE_RANGE, site: str = "") -> dict:
    if range_key not in ISSUE_RANGES:
        raise BadRequest(f"range must be one of {list(ISSUE_RANGES)}")
    spec, units = _spec(product_id)
    issued_at = _parse_issued(issued)
    qs = Forecast.objects.filter(product_id=product_id, site=site)
    if issued_at is None:
        latest = qs.order_by("-issued_at", "-inserted_at").values_list("issued_at", "model_version").first()
    else:
        latest = qs.filter(issued_at=issued_at).order_by("-inserted_at").values_list("issued_at", "model_version").first()
    if latest is None:
        out = _header(product_id, spec, units, None)
        out.update(view="issue", issued_at=None, rows={k: [] for k in ("valid_at", "lead_seconds", "value", "lower", "upper")},
                   observed=series.bucketed_series(spec["target"]["product"], range_key))
        return out
    issued_at, model_version = latest
    field = spec["target"]["field"]
    rows = list(qs.filter(issued_at=issued_at, model_version=model_version).order_by("valid_at")
                .values_list("valid_at", "lead_seconds", "values"))
    out = _header(product_id, spec, units, model_version)
    out.update(
        view="issue",
        issued_at=_epoch(issued_at),
        rows={
            "valid_at": [_epoch(v) for v, _l, _x in rows],
            "lead_seconds": [lead for _v, lead, _x in rows],
            "value": [x.get(field, {}).get("value") for _v, _l, x in rows],
            "lower": [x.get(field, {}).get("lower") for _v, _l, x in rows],
            "upper": [x.get(field, {}).get("upper") for _v, _l, x in rows],
        },
        observed=series.bucketed_series(spec["target"]["product"], range_key, now=issued_at),
    )
    return out


def lead_view(product_id: str, *, lead: str, range_key: str = DEFAULT_LEAD_RANGE, site: str = "",
              now: dt.datetime | None = None) -> dict:
    if range_key not in LEAD_RANGES:
        raise BadRequest(f"range must be one of {list(LEAD_RANGES)} with lead=")
    spec, units = _spec(product_id)
    try:
        lead_seconds = parse_duration(lead)
    except ValueError:
        raise BadRequest(f"lead must be a duration like '3h', got {lead!r}") from None
    if lead not in spec["horizons"] and lead_seconds not in [parse_duration(h) for h in spec["horizons"]]:
        raise BadRequest(f"lead {lead!r} is not one of this model's horizons {spec['horizons']}")
    now = now or timezone.now()
    since = now - dt.timedelta(days=LEAD_RANGES[range_key])
    tolerance = swxkit.HORIZON_TOLERANCE_SECONDS
    qs = Forecast.objects.filter(
        product_id=product_id, site=site, valid_at__gt=since, valid_at__lte=now,
        lead_seconds__gte=lead_seconds - tolerance, lead_seconds__lte=lead_seconds + tolerance,
    )
    model_version = qs.order_by("-issued_at").values_list("model_version", flat=True).first()
    field = spec["target"]["field"]
    rows = list(qs.filter(model_version=model_version).order_by("valid_at").values_list("valid_at", "values"))
    target, step = spec["target"]["product"], None
    observations = swxkit.read_series(target, field, since - dt.timedelta(days=1), site=site) if rows else []
    step = forecasting.target_step(observations)
    times = [t for t, _v in observations]
    observed = []
    for valid_at, _x in rows:
        match = forecasting.matching_observation(observations, valid_at, step, times=times) if step else None
        observed.append(match[1] if match else None)
    out = _header(product_id, spec, units, model_version)
    out.update(
        view="lead",
        lead_seconds=lead_seconds,
        pairs={
            "valid_at": [_epoch(v) for v, _x in rows],
            "forecast": [x.get(field, {}).get("value") for _v, x in rows],
            "lower": [x.get(field, {}).get("lower") for _v, x in rows],
            "upper": [x.get(field, {}).get("upper") for _v, x in rows],
            "observed": observed,
        },
    )
    return out


def horizons(product_id: str) -> list[tuple[str, int]]:
    return [(h, parse_duration(h)) for h in get_manifest(product_id)["forecast"]["horizons"]]


# The chart's observed panel for a target reuses its own product's panel
# spec (Kp: severity-coloured bars, D-062) -- see charts.PRODUCT_PANELS.
def target_panel(product_id: str):
    target = get_manifest(product_id)["forecast"]["target"]["product"]
    panels = charts.PRODUCT_PANELS.get(target, ())
    return target, panels[0] if panels else None
