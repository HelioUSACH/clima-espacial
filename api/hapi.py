"""A read-only HAPI server under /hapi/ (D-013, D-088): about, capabilities,
catalog, info, data, for the datasets `api.export` says are exportable.

Targets HAPI 3.3 (specification document 3.3.1, the latest published at
https://github.com/hapi-server/data-specification when this was written).
As HAPI 3 requires, the 2.x request names `id`, `time.min` and `time.max`
are accepted as well as `dataset`, `start` and `stop`.

Every response carries `Access-Control-Allow-Origin: *` (spec §5.1), and
every error is a HAPI status JSON with the spec's code, also in the HTTP
reason phrase. `data` needs an API key except for the keyless preview
(D-083); a missing or invalid key is HTTP 401 with status 1400, an
exhausted quota HTTP 429 with status 1400 and Retry-After. Output formats:
csv and json (no binary).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json

from django.conf import settings
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.http import http_date
from django.views.decorators.http import require_http_methods

from . import export
from .access import Denied, authorize, is_preview, record_usage, registration_url

HAPI_VERSION = "3.3"
SPEC_DOCUMENT = "HAPI 3.3.1"
OUTPUT_FORMATS = ("csv", "json")
SERVER_ID = "USACH/ClimaEspacial"

STATUS_MESSAGES = {
    1200: "OK",
    1201: "OK - no data for time range",
    1400: "Bad request - user input error",
    1401: "Bad request - unknown API parameter name",
    1402: "Bad request - syntax error in start time",
    1403: "Bad request - syntax error in stop time",
    1404: "Bad request - start equal to or after stop",
    1406: "Bad request - unknown dataset id",
    1407: "Bad request - unknown dataset parameter",
    1408: "Bad request - too much time or data requested",
    1409: "Bad request - unsupported output format",
    1410: "Bad request - unsupported include value",
    1411: "Bad request - out-of-order or duplicate parameters",
    1412: "Bad request - unsupported resolve_references value",
    1413: "Bad request - unsupported depth value",
    1500: "Internal server error",
}
_HTTP_REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 429: "Too Many Requests",
                 500: "Internal Server Error"}

_COMMON_PARAMS = {"api_key"}
_INFO_PARAMS = {"dataset", "id", "parameters", "resolve_references"} | _COMMON_PARAMS
_DATA_PARAMS = {"dataset", "id", "start", "stop", "time.min", "time.max", "parameters", "include", "format"} | _COMMON_PARAMS
_CATALOG_PARAMS = {"depth", "resolve_references"} | _COMMON_PARAMS

LICENSE_URLS = {"cc-by-4.0": "https://spdx.org/licenses/CC-BY-4.0.html",
                "cc by 4.0": "https://spdx.org/licenses/CC-BY-4.0.html",
                "cc0-1.0": "https://spdx.org/licenses/CC0-1.0.html"}
METADATA_MAX_AGE = 60


class HapiError(Exception):
    def __init__(self, code: int, http_status: int, message: str | None = None, *, retry_after: int | None = None):
        super().__init__(message or STATUS_MESSAGES.get(code, ""))
        self.code = code
        self.http_status = http_status
        self.message = message or STATUS_MESSAGES.get(code, "")
        self.retry_after = retry_after


def _status(code: int, message: str | None = None) -> dict:
    # An error message starts "HAPI error NNNN:", as the HAPI verifier
    # prefers, so a client that only shows the text still sees the code.
    text = message or STATUS_MESSAGES[code]
    return {"code": code, "message": text if code < 1400 else f"HAPI error {code}: {text}"}


def _cors(response):
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    return response


# Metadata that changes only with a deploy (about, capabilities) is "last
# modified" when this process started.
_STARTED = timezone.now()


def _json_response(payload: dict, *, http_status: int = 200, code: int = 1200, max_age: int | None = None,
                   last_modified: dt.datetime | None = None):
    response = HttpResponse(json.dumps(payload, indent=1, ensure_ascii=False) + "\n",
                            content_type="application/json; charset=utf-8", status=http_status)
    if code != 1200:
        response.reason_phrase = f"{_HTTP_REASONS.get(http_status, 'Error')}; HAPI {code} {STATUS_MESSAGES.get(code, '')}"
    response["Cache-Control"] = f"public, max-age={max_age}" if max_age else "no-store"
    if last_modified is not None:
        response["Last-Modified"] = http_date(last_modified.timestamp())
    return _cors(response)


def _error_response(exc: HapiError):
    response = _json_response({"HAPI": HAPI_VERSION, "status": _status(exc.code, exc.message)},
                              http_status=exc.http_status, code=exc.code)
    if exc.retry_after:
        response["Retry-After"] = str(exc.retry_after)
    return response


def _check_params(request, allowed: set[str]) -> None:
    if any(name not in allowed for name in request.GET):
        raise HapiError(1401, 400)


def _check_resolve_references(request) -> None:
    if request.GET.get("resolve_references", "true") not in ("true", "false"):
        raise HapiError(1412, 400)


def hapi_view(allowed_params):
    """Common wrapper: GET/HEAD/OPTIONS only, unknown request parameters
    are 1401, every HapiError becomes a HAPI status response."""
    def decorate(func):
        @require_http_methods(["GET", "HEAD", "OPTIONS"])
        def wrapper(request, *args, **kwargs):
            if request.method == "OPTIONS":
                return _cors(HttpResponse(status=204))
            try:
                if allowed_params is not None:
                    _check_params(request, allowed_params)
                return func(request, *args, **kwargs)
            except HapiError as exc:
                return _error_response(exc)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorate


# --- metadata --------------------------------------------------------------------

def _listed(ds: export.Dataset) -> bool:
    return export.time_bounds(ds)[0] is not None


def _listed_datasets() -> list[export.Dataset]:
    # A dataset with nothing stored yet has no startDate, which `info`
    # requires: it joins the catalog with its first data.
    return [ds for ds in export.datasets() if _listed(ds)]


def _dataset_or_error(request) -> export.Dataset:
    dataset_id = request.GET.get("dataset") or request.GET.get("id")
    if not dataset_id:
        raise HapiError(1400, 400, "Bad request - user input error: the dataset parameter is required")
    ds = export.get_dataset(dataset_id)
    if ds is None or not _listed(ds):
        raise HapiError(1406, 404)
    return ds


def _sample_window(first: dt.datetime, last: dt.datetime, now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """The last complete UTC day with data, so a verifier's sample request
    fits the keyless preview (D-083): ≤ 24 h, recent."""
    day_end = min(last, now).astimezone(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = max(first, day_end - dt.timedelta(days=1))
    if start >= day_end:  # less than a complete day stored: whatever there is
        return first, last + dt.timedelta(seconds=1)
    return start, day_end


def _parameter_json(column: export.Column) -> dict:
    entry = {"name": column.name, "type": column.type, "units": column.units}
    if column.type == "string":
        entry["length"] = export.ATTRIBUTE_LENGTH
        entry["fill"] = export.STRING_FILL
    else:
        entry["fill"] = export.FILL_TEXT
    entry["label"] = column.label
    if column.description:
        entry["description"] = column.description
    return entry


def _info(request, ds: export.Dataset, columns: tuple[export.Column, ...]) -> dict:
    first, last = export.time_bounds(ds)
    sample_start, sample_stop = _sample_window(first, last, timezone.now())
    info = {
        "HAPI": HAPI_VERSION,
        "status": _status(1200),
        "parameters": [{"name": "Time", "type": "isotime", "units": "UTC", "fill": None,
                        "length": export.TIME_LENGTH}] + [_parameter_json(c) for c in columns],
        "startDate": export.fmt_time(first),
        "stopDate": export.fmt_time(last),
        "timeStampLocation": "begin",
        "cadence": export.iso_duration(export.native_step_seconds(ds)),
        "sampleStartDate": export.fmt_time(sample_start),
        "sampleStopDate": export.fmt_time(sample_stop),
        "maxRequestDuration": f"P{export.max_span(ds).days}D",
        "description": (ds.manifest.get("description") or {}).get("en") or ds.title["en"],
        "resourceURL": export.product_page_url(request, ds.id),
        "x_title": ds.title["en"],
        "x_license": ds.license,
        "x_provider": ds.provider,
        "x_relay": ds.relay,
        "x_fill_note": f"{export.FILL_TEXT} marks a missing value or a provider fill value.",
    }
    license_url = LICENSE_URLS.get(ds.license.strip().lower())
    if license_url:
        info["licenseURL"] = license_url
    if ds.citation:
        info["datasetCitation"] = ds.citation
    if ds.provider_url:
        info["x_provider_url"] = ds.provider_url
    if ds.relay:
        info["x_relay_note"] = export.RELAY_NOTE.format(url=ds.provider_url or ds.provider)
    if ds.channel_key:
        info["x_layout_note"] = (f"The provider sends one row per {ds.channel_key}; each is a column here, "
                                 f"plus the satellite the values came from.")
    return info


def landing(request):
    """`/hapi`: a short human page (spec §3.2)."""
    with translation.override("en"):
        response = render(request, "api/hapi_landing.html", {
            "datasets": _listed_datasets(), "hapi_version": HAPI_VERSION, "spec": SPEC_DOCUMENT,
            "data_api_url": registration_url(request),
        })
    return _cors(response)


@hapi_view(_COMMON_PARAMS)
def about(request):
    contact = settings.SITE_CONTACT_EMAIL or registration_url(request)
    payload = {
        "HAPI": HAPI_VERSION,
        "status": _status(1200),
        "id": SERVER_ID,
        "title": "Clima Espacial USACH",
        "contact": contact,
        "description": ("Space-weather data from the Clima Espacial USACH portal (HelioFísica USACH, "
                        "Universidad de Santiago de Chile). Products made at USACH, plus some third-party "
                        "series relayed for convenience; each dataset keeps its own licence."),
        "note": [f"Implements {SPEC_DOCUMENT} (HAPI {HAPI_VERSION}); output formats csv and json.",
                 "data requests need a free API key (X-API-Key header or api_key parameter), except short "
                 f"recent requests: at most {settings.SWX_API_KEYLESS_MAX_HOURS} h ending within the last "
                 f"{settings.SWX_API_KEYLESS_RECENT_DAYS} days. Register at {registration_url(request)}"],
        "x_specification": SPEC_DOCUMENT,
    }
    listed = _listed_datasets()
    if listed:
        ds = listed[0]
        first, last = export.time_bounds(ds)
        start, stop = _sample_window(first, last, timezone.now())
        payload["dataTest"] = {"name": "Last complete day", "query": {
            "dataset": ds.id, "start": export.fmt_time(start), "stop": export.fmt_time(stop),
            "parameters": ",".join(c.name for c in ds.columns)}}
    return _json_response(payload, max_age=METADATA_MAX_AGE, last_modified=_STARTED)


@hapi_view(_COMMON_PARAMS)
def capabilities(request):
    return _json_response({"HAPI": HAPI_VERSION, "status": _status(1200), "outputFormats": list(OUTPUT_FORMATS),
                           "catalogDepthOptions": ["dataset", "all"]}, max_age=METADATA_MAX_AGE,
                          last_modified=_STARTED)


@hapi_view(_CATALOG_PARAMS)
def catalog(request):
    _check_resolve_references(request)
    depth = request.GET.get("depth", "dataset")
    if depth not in ("dataset", "all"):
        raise HapiError(1413, 400)
    entries, newest = [], _STARTED
    for ds in _listed_datasets():
        newest = max(newest, export.time_bounds(ds)[1])
        entry = {"id": ds.id, "title": ds.title["en"]}
        if depth == "all":
            info = _info(request, ds, ds.columns)
            info.pop("HAPI")
            info.pop("status")
            entry["info"] = info
        entries.append(entry)
    return _json_response({"HAPI": HAPI_VERSION, "status": _status(1200), "catalog": entries},
                          max_age=METADATA_MAX_AGE, last_modified=newest)


@hapi_view(_INFO_PARAMS)
def info(request):
    _check_resolve_references(request)
    ds = _dataset_or_error(request)
    columns = _columns_or_error(ds, request.GET.get("parameters"))
    return _json_response(_info(request, ds, columns), max_age=METADATA_MAX_AGE,
                          last_modified=max(_STARTED, export.time_bounds(ds)[1]))


def _columns_or_error(ds, raw):
    try:
        return export.select_columns(ds, raw)
    except export.ExportError as exc:
        raise HapiError(exc.code, exc.http_status) from None


# --- data --------------------------------------------------------------------------

def _time_param(request, names, code) -> dt.datetime:
    raw = next((request.GET[n] for n in names if request.GET.get(n)), "")
    if not raw:
        raise HapiError(code, 400)
    try:
        return export.parse_time(raw)
    except ValueError:
        raise HapiError(code, 400) from None


def _csv_chunks(rows, buffer_lines=500):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    n = 0
    for time_tag, values in rows:
        writer.writerow([export.fmt_time(time_tag), *(export.fmt_value(v) for v in values)])
        n += 1
        if n % buffer_lines == 0:
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
    if buf.getvalue():
        yield buf.getvalue()


def _json_value(value):
    return export.FILL if value is None else value


def _json_chunks(header: dict, rows, buffer_lines=500):
    head = json.dumps(header, indent=1, ensure_ascii=False)
    yield head[:-1].rstrip() + ',\n"data": [\n'
    parts, first = [], True
    for time_tag, values in rows:
        record = json.dumps([export.fmt_time(time_tag), *(_json_value(v) for v in values)], ensure_ascii=False)
        parts.append(record if first else ",\n" + record)
        first = False
        if len(parts) >= buffer_lines:
            yield "".join(parts)
            parts = []
    parts.append("\n]\n}\n")
    yield "".join(parts)


@hapi_view(_DATA_PARAMS)
def data(request):
    ds = _dataset_or_error(request)
    start = _time_param(request, ("start", "time.min"), 1402)
    stop = _time_param(request, ("stop", "time.max"), 1403)
    fmt = request.GET.get("format") or "csv"
    if fmt not in OUTPUT_FORMATS:
        raise HapiError(1409, 400)
    include = request.GET.get("include", "")
    if include not in ("", "header"):
        raise HapiError(1410, 400)
    columns = _columns_or_error(ds, request.GET.get("parameters"))
    try:
        export.check_span(ds, start, stop)
        n = export.count_rows(ds, start, stop)
        export.check_rows(ds, n)
    except export.ExportError as exc:
        raise HapiError(exc.code, exc.http_status, exc.message) from None
    try:
        key = authorize(request, preview_ok=is_preview(start, stop), rows=n)
    except Denied as exc:
        raise HapiError(1400, exc.status, exc.message, retry_after=exc.retry_after) from None
    if request.method == "GET":
        record_usage(key, "hapi", n)

    code = 1200 if n else 1201
    rows = export.iter_rows(ds, start, stop, columns) if n else iter(())
    if fmt == "json":
        header = _info(request, ds, columns)
        header["status"] = _status(code)
        header["format"] = "json"
        response = StreamingHttpResponse(_json_chunks(header, rows), content_type="application/json; charset=utf-8")
    else:
        body = _csv_chunks(rows)
        if include == "header":
            header = _info(request, ds, columns)
            header["status"] = _status(code)
            header["format"] = "csv"
            text = "".join("#" + line + "\n" for line in json.dumps(header, indent=1, ensure_ascii=False).split("\n"))
            body = _prepend(text, body)
        response = StreamingHttpResponse(body, content_type="text/csv; charset=utf-8")
    if code == 1201:
        response.reason_phrase = "OK; HAPI 1201 OK - no data for time range"
    response["Cache-Control"] = "no-store"
    return _cors(response)


def _prepend(first: str, rest):
    yield first
    yield from rest


def unknown_endpoint(request, rest=""):
    """Spec §4.1: an endpoint that doesn't exist is 1400."""
    return _error_response(HapiError(1400, 400, "Bad request - user input error: unknown HAPI endpoint; "
                                                "see " + request.build_absolute_uri(reverse("hapi:landing"))))
