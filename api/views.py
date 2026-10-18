"""Language-neutral data endpoints under /api/v1/ (outside i18n_patterns):

- `datos/<product_id>.csv?start=…&stop=…`: a CSV download (§3, D-087),
  the same rows as HAPI `data`, with a `#` header block.
- `pronosticos/<product_id>.json|.csv[?issued_since=…&issued_until=…]`: a
  forecast product's issues in the D-076 exchange format (D-089). Without
  parameters, the latest issue.

Same access rules as HAPI (`api.access`): a key, or the keyless preview.
Errors are JSON `{"error": "..."}` with the HTTP status.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.manifests.loader import discover_manifests, license_allows_redistribution
from core.models import Forecast

from . import export
from .access import Denied, authorize, is_preview, record_usage


class ApiError(Exception):
    def __init__(self, http_status: int, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        self.http_status = http_status
        self.message = message
        self.retry_after = retry_after


def _error(exc: ApiError):
    response = JsonResponse({"error": exc.message}, status=exc.http_status, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "no-store"
    if exc.retry_after:
        response["Retry-After"] = str(exc.retry_after)
    return response


def _authorize(request, *, preview_ok, rows):
    try:
        return authorize(request, preview_ok=preview_ok, rows=rows)
    except Denied as exc:
        raise ApiError(exc.status, exc.message, retry_after=exc.retry_after) from None


def _time(request, name: str, *, required: bool = True) -> dt.datetime | None:
    raw = request.GET.get(name, "")
    if not raw:
        if required:
            raise ApiError(400, f"{name} is required (ISO 8601 UTC, e.g. 2026-10-17T00:00:00Z)")
        return None
    try:
        return export.parse_time(raw)
    except ValueError:
        raise ApiError(400, f"{name} must be an ISO 8601 UTC time, e.g. 2026-10-17T00:00:00Z") from None


def _lines(rows):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for i, row in enumerate(rows, 1):
        writer.writerow(row)
        if i % 500 == 0:
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
    if buf.getvalue():
        yield buf.getvalue()


def _attachment(response, filename: str):
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-store"
    return response


def _stamp(when: dt.datetime) -> str:
    return when.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


@require_http_methods(["GET", "HEAD"])
def download_csv(request, product_id):
    try:
        ds = export.get_dataset(product_id)
        if ds is None:
            raise ApiError(404, f"{product_id} is not available for download: see /datos-api/ for the list")
        start, stop = _time(request, "start"), _time(request, "stop")
        try:
            export.check_span(ds, start, stop)
            n = export.count_rows(ds, start, stop)
            export.check_rows(ds, n)
        except export.ExportError as exc:
            raise ApiError(400, exc.message) from None
        key = _authorize(request, preview_ok=is_preview(start, stop), rows=n)
    except ApiError as exc:
        return _error(exc)
    if request.method == "GET":
        record_usage(key, "download", n)

    header = export.csv_header_lines(request, ds, ds.columns, retrieved_at=timezone.now())

    def body():
        yield "\n".join(header) + "\n"
        rows = export.iter_rows(ds, start, stop, ds.columns) if n else ()
        yield from _lines([["time", *(c.name for c in ds.columns)]])
        yield from _lines([export.fmt_time(t), *(export.fmt_value(v) for v in values)] for t, values in rows)

    response = StreamingHttpResponse(body(), content_type="text/csv; charset=utf-8")
    return _attachment(response, f"{ds.id}_{_stamp(start)}_{_stamp(stop)}.csv")


# --- forecasts (D-076 exchange format) ------------------------------------------

EXCHANGE_FIELDS = ("model_id", "model_version", "issued_at", "valid_at", "lead_seconds", "site", "variable",
                   "units", "value", "lower", "upper", "interval_level", "provenance")


def _forecast_manifest(product_id: str) -> dict:
    manifest = discover_manifests().get(product_id)
    if (manifest is None or manifest.get("kind") != "forecast"
            or not license_allows_redistribution(manifest["source"].get("license", ""))):
        raise ApiError(404, f"{product_id} is not a forecast product available for export")
    return manifest


def _target_units(manifest: dict) -> str:
    target = manifest["forecast"]["target"]
    target_manifest = discover_manifests().get(target["product"], {})
    param = next((p for p in target_manifest.get("parameters", []) if p["name"] == target["field"]), {})
    return param.get("units", "")


def exchange_records(manifest: dict, rows) -> list[dict]:
    """Stored forecast rows -> D-076 records (docs/models/INTEGRATION.md):
    the stored `values` keys are the format's own, so this only reshapes."""
    spec = manifest["forecast"]
    field = spec["target"]["field"]
    units = _target_units(manifest)
    level = spec["uncertainty"].get("level") if spec["uncertainty"]["type"] == "bounds" else None
    records = []
    for row in rows:
        values = row.values.get(field) or {}
        records.append({
            "model_id": row.product_id,
            "model_version": row.model_version,
            "issued_at": export.fmt_time(row.issued_at),
            "valid_at": export.fmt_time(row.valid_at),
            "lead_seconds": row.lead_seconds,
            "site": row.site,
            "variable": field,
            "units": units,
            "value": values.get("value"),
            "lower": values.get("lower") if level is not None else None,
            "upper": values.get("upper") if level is not None else None,
            "interval_level": level,
            "provenance": row.provenance,
        })
    return records


def _forecast_rows(product_id: str, since, until):
    qs = Forecast.objects.filter(product_id=product_id)
    if since is None:
        latest = qs.order_by("-issued_at").values_list("issued_at", flat=True).first()
        if latest is None:
            return qs.none()
        qs = qs.filter(issued_at=latest)
    else:
        qs = qs.filter(issued_at__gte=since, issued_at__lte=until)
    return qs.order_by("issued_at", "valid_at", "site", "model_version")


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, dict):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return value


@require_http_methods(["GET", "HEAD"])
def forecast_export(request, product_id, fmt):
    try:
        manifest = _forecast_manifest(product_id)
        since = _time(request, "issued_since", required=False)
        until = _time(request, "issued_until", required=False)
        if since is None and until is not None:
            raise ApiError(400, "issued_until needs issued_since")
        if since is not None:
            until = until or timezone.now()
            if since > until:
                raise ApiError(400, "issued_since is after issued_until")
            if until - since > dt.timedelta(days=settings.SWX_FORECAST_MAX_DAYS):
                raise ApiError(400, f"at most {settings.SWX_FORECAST_MAX_DAYS} days of issues per request")
        rows = _forecast_rows(product_id, since, until)
        n = rows.count()
        if n > settings.SWX_EXPORT_MAX_ROWS:
            raise ApiError(400, f"{n} records, at most {settings.SWX_EXPORT_MAX_ROWS} per request")
        key = _authorize(request, preview_ok=since is None, rows=n)
    except ApiError as exc:
        return _error(exc)
    if request.method == "GET":
        record_usage(key, "forecast", n)

    records = exchange_records(manifest, rows)
    retrieved = timezone.now()
    page = export.product_page_url(request, product_id)
    citation = (manifest.get("facts", {}).get("citation") or {}).get("en", "")
    if fmt == "json":
        envelope = {
            "product": product_id,
            "title": manifest["title"],
            "format": "forecast exchange format (D-076), one record per (issue, valid time, site)",
            "product_page": page,
            "provider": manifest["source"]["provider"],
            "license": manifest["source"].get("license", ""),
            "citation": citation or None,
            "retrieved_at": export.fmt_time(retrieved),
            "records": records,
        }
        response = JsonResponse(envelope, json_dumps_params={"ensure_ascii": False, "indent": 1})
        response["Cache-Control"] = "no-store"
        return response

    header = [
        f"# {manifest['title']['en']} / {manifest['title']['es']}",
        f"# Product page: {page}",
        f"# Source: {manifest['source']['provider']}",
        f"# Licence: {manifest['source'].get('license', '')}",
    ]
    if citation:
        header.append(f"# Citation: {citation}")
    header += [f"# Retrieved: {export.fmt_time(retrieved)} from Clima Espacial USACH",
               "# Format: forecast exchange format (D-076); provenance is compact JSON; empty field = null"]

    def body():
        yield "\n".join(header) + "\n"
        yield from _lines([list(EXCHANGE_FIELDS)])
        yield from _lines([_csv_value(r[f]) for f in EXCHANGE_FIELDS] for r in records)

    response = StreamingHttpResponse(body(), content_type="text/csv; charset=utf-8")
    suffix = "latest" if since is None else f"{_stamp(since)}_{_stamp(until)}"
    return _attachment(response, f"{product_id}_{suffix}.csv")

