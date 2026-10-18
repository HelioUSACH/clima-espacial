"""The one export layer behind HAPI and CSV downloads (D-087).

What is exportable is read from the manifests, never listed in code: a
`timeseries`/`index` product with `exchange.hapi: true` whose licence allows
redistribution (D-078), and whose manifest still exists (a retired product
has none). Withdrawing a dataset is flipping `exchange.hapi` to false.

Each dataset is classed `own` (its provider is USACH) or `relay` (a third
party's data we pass on). Relaying is transitional (D-084): a relay dataset
says so, and points to the provider's official page, wherever it is offered.
Nothing here depends on which class a dataset is.

Rows are the raw stored samples, ordered by `time_tag`, no bucketing,
streamed from a server-side cursor. A revisable series (D-080) exports its
current stored value. A long-format product (several rows per `time_tag`,
one per `energy` channel: GOES) is pivoted into one column per channel, plus
the satellite as a string column.

Values: a number, or the fill value when the stored value is missing,
not a number, or a provider fill (at or below -9999, the same rule as the
charts, D-056).
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from itertools import groupby

from django.conf import settings
from django.core.cache import cache
from django.db.models import Max, Min
from django.urls import reverse
from django.utils import timezone, translation

from core.manifests.loader import (
    CHANNEL_KEYS,
    discover_manifests,
    is_own_provider,
    license_allows_redistribution,
    parse_duration,
)
from core.models import TimeSeriesPoint

EXPORTABLE_KINDS = ("timeseries", "index")
# In a long-format product, a per-time_tag attribute exported as a string
# column instead of being pivoted (which GOES satellite the rows came from).
ROW_ATTRIBUTES = ("satellite",)
ATTRIBUTE_LENGTH = 16
STRING_FILL = "none"  # a string column's fill (HAPI requires one per parameter)

FILL = -1.0e31
FILL_TEXT = "-1.0E31"
PROVIDER_FILL_CEILING = -9999  # web/charts.py FILL_VALUE_CEILING (D-056)
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
TIME_LENGTH = 20

FAST_CADENCE_SECONDS = 300  # <= 5 min: the short span cap applies
CHUNK_ROWS = 2000


class ExportError(ValueError):
    """A request the export layer refuses: a HAPI status `code`, the HTTP
    status that goes with it, and a message safe to show the client."""

    def __init__(self, code: int, http_status: int, message: str):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.message = message


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # "double" | "string"
    units: str | None
    label: str
    param: str  # the manifest parameter it comes from
    channel: str | None = None  # long format: the channel value this column holds
    description: str = ""


@dataclass(frozen=True)
class Dataset:
    id: str
    manifest: dict = field(repr=False)
    columns: tuple[Column, ...]
    channel_key: str | None

    @property
    def title(self) -> dict:
        return self.manifest["title"]

    @property
    def source(self) -> dict:
        return self.manifest["source"]

    @property
    def provider(self) -> str:
        return self.source["provider"]

    @property
    def own(self) -> bool:
        return is_own_provider(self.provider)

    @property
    def relay(self) -> bool:
        return not self.own

    @property
    def provider_url(self) -> str:
        links = self.source.get("links") or []
        return links[0]["url"] if links else ""

    @property
    def license(self) -> str:
        return self.source.get("license", "")

    def fact(self, key: str, lang: str = "en") -> str:
        return (self.manifest.get("facts", {}).get(key) or {}).get(lang, "")

    @property
    def citation(self) -> str:
        return self.fact("citation", "en")

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


def _units(raw: str | None) -> str | None:
    if raw is None or raw == "":
        return None
    return "dimensionless" if raw == "1" else raw


def channel_column_name(param: str, channel: str) -> str:
    """"flux" + ">=10 MeV" -> "flux_ge10MeV"; "0.1-0.8nm" -> "flux_0.1-0.8nm".
    Only characters that need no URL encoding (HAPI spec §8.2)."""
    text = channel.replace(">=", "ge").replace("<=", "le").replace(">", "gt").replace("<", "lt")
    return f"{param}_" + re.sub(r"[^A-Za-z0-9._-]", "", text)


def _columns(manifest: dict) -> tuple[tuple[Column, ...], str | None]:
    params = manifest.get("parameters", [])
    names = [p["name"] for p in params]
    channel_key = next((k for k in CHANNEL_KEYS if k in names), None)
    if channel_key is None:
        return tuple(Column(p["name"], "double", _units(p.get("units")), p["name"], p["name"])
                     for p in params if "enum" not in p), None
    channels = next(p for p in params if p["name"] == channel_key)["enum"]
    columns = []
    for p in params:
        if p["name"] == channel_key or p["name"] in ROW_ATTRIBUTES or "enum" in p:
            continue
        for ch in channels:
            columns.append(Column(channel_column_name(p["name"], ch), "double", _units(p.get("units")),
                                  f"{p['name']} {ch}", p["name"], channel=ch,
                                  description=f"{p['name']}, {channel_key} {ch}"))
    for p in params:
        if p["name"] in ROW_ATTRIBUTES:
            columns.append(Column(p["name"], "string", None, p["name"], p["name"],
                                  description=f"{p['name']} the row came from"))
    return tuple(columns), channel_key


def is_exportable(manifest: dict) -> bool:
    return (manifest.get("kind") in EXPORTABLE_KINDS
            and bool(manifest.get("exchange", {}).get("hapi"))
            and license_allows_redistribution(manifest.get("source", {}).get("license", "")))


def datasets() -> list[Dataset]:
    out = []
    for product_id, manifest in sorted(discover_manifests().items()):
        if is_exportable(manifest):
            columns, channel_key = _columns(manifest)
            out.append(Dataset(product_id, manifest, columns, channel_key))
    return out


def get_dataset(product_id: str) -> Dataset | None:
    return next((d for d in datasets() if d.id == product_id), None)


def not_exported() -> list[dict]:
    """Observed products the portal shows but doesn't export (a licence
    that forbids it, or `exchange.hapi: false`): /datos-api/ links their
    providers instead."""
    return [m for _pid, m in sorted(discover_manifests().items())
            if m.get("kind") in EXPORTABLE_KINDS and not is_exportable(m)]


# --- time ----------------------------------------------------------------------

_TIME_RE = re.compile(
    r"^(?P<y>\d{4})(?:-(?:(?P<doy>\d{3})|(?P<m>\d{2})(?:-(?P<d>\d{2}))?))?"
    r"(?:T(?P<H>\d{2})(?::(?P<M>\d{2})(?::(?P<S>\d{2})(?:\.(?P<f>\d{1,9}))?)?)?)?Z?$"
)


def parse_time(raw: str) -> dt.datetime:
    """HAPI's restricted ISO 8601 (spec §3.7.6.1): yyyy-mm-dd or yyyy-ddd,
    truncated forms (missing fields take their smallest value), `Z` optional
    and UTC either way. An explicit offset ("+00:00") is also accepted.
    Raises ValueError."""
    raw = (raw or "").strip()
    match = _TIME_RE.match(raw)
    if match:
        g = match.groupdict()
        if g["doy"]:
            base = dt.datetime(int(g["y"]), 1, 1, tzinfo=dt.UTC) + dt.timedelta(days=int(g["doy"]) - 1)
            if base.year != int(g["y"]):
                raise ValueError("day of year out of range")
        else:
            base = dt.datetime(int(g["y"]), int(g["m"] or 1), int(g["d"] or 1), tzinfo=dt.UTC)
        hour, minute, second = int(g["H"] or 0), int(g["M"] or 0), int(g["S"] or 0)
        # hour 24 (= the next midnight) and second 60 (a leap second) are
        # valid HAPI times; anything else out of range is not.
        if hour > 24 or minute > 59 or second > 60 or (hour == 24 and (minute or second or g["f"])):
            raise ValueError("time of day out of range")
        micro = int((g["f"] or "0").ljust(6, "0")[:6])
        return base + dt.timedelta(hours=int(g["H"] or 0), minutes=int(g["M"] or 0), seconds=int(g["S"] or 0),
                                   microseconds=micro)
    when = dt.datetime.fromisoformat(raw)
    if timezone.is_naive(when):
        when = when.replace(tzinfo=dt.UTC)
    return when.astimezone(dt.UTC)


def fmt_time(when: dt.datetime) -> str:
    return when.astimezone(dt.UTC).strftime(TIME_FORMAT)


def iso_duration(seconds: int) -> str:
    """3600 -> "PT1H", 60 -> "PT1M", 86400 -> "P1D", 90 -> "PT1M30S"."""
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    out = "P" + (f"{days}D" if days else "")
    clock = (f"{hours}H" if hours else "") + (f"{minutes}M" if minutes else "") + (f"{secs}S" if secs else "")
    return out + ("T" + clock if clock else "") if (days or clock) else "PT0S"


# --- what is stored ------------------------------------------------------------

_META_CACHE_SECONDS = 60


def time_bounds(ds: Dataset) -> tuple[dt.datetime | None, dt.datetime | None]:
    """(first, last) stored time_tag, cached for a minute."""
    cache_key = f"swx:export:bounds:{ds.id}"
    cached = cache.get(cache_key)
    if cached is None:
        agg = TimeSeriesPoint.objects.filter(product_id=ds.id).aggregate(first=Min("time_tag"), last=Max("time_tag"))
        cached = (agg["first"], agg["last"])
        cache.set(cache_key, cached, _META_CACHE_SECONDS)
    return cached


def native_step_seconds(ds: Dataset) -> int:
    """The data's own cadence: the median spacing of the newest stored
    time_tags (not the manifest's polling `cadence`: noaa.kp is polled every
    15 min for 3-hourly data). Falls back to the manifest cadence while
    there is too little data. Cached for an hour."""
    cache_key = f"swx:export:step:{ds.id}"
    step = cache.get(cache_key)
    if step is None:
        times = list(TimeSeriesPoint.objects.filter(product_id=ds.id).order_by("-time_tag")
                     .values_list("time_tag", flat=True).distinct()[:200])
        deltas = sorted(int((a - b).total_seconds()) for a, b in zip(times, times[1:], strict=False))
        deltas = [d for d in deltas if d > 0]
        step = deltas[len(deltas) // 2] if len(deltas) >= 2 else parse_duration(ds.manifest["cadence"])
        cache.set(cache_key, step, 3600)
    return step


def max_span(ds: Dataset) -> dt.timedelta:
    fast = native_step_seconds(ds) <= FAST_CADENCE_SECONDS
    return dt.timedelta(days=settings.SWX_EXPORT_MAX_DAYS_FAST if fast else settings.SWX_EXPORT_MAX_DAYS_SLOW)


def check_span(ds: Dataset, start: dt.datetime, stop: dt.datetime) -> None:
    if start >= stop:
        raise ExportError(1404, 400, "Bad request - start equal to or after stop")
    limit = max_span(ds)
    if stop - start > limit:
        raise ExportError(1408, 400, f"Bad request - too much time or data requested: at most {limit.days} days "
                                     f"per request for {ds.id}; split the range into several requests")


def count_rows(ds: Dataset, start: dt.datetime, stop: dt.datetime) -> int:
    qs = TimeSeriesPoint.objects.filter(product_id=ds.id, time_tag__gte=start, time_tag__lt=stop)
    if ds.channel_key:
        return qs.values("time_tag").distinct().count()
    return qs.count()


def check_rows(ds: Dataset, n: int) -> None:
    if n > settings.SWX_EXPORT_MAX_ROWS:
        raise ExportError(1408, 400, f"Bad request - too much time or data requested: {n} rows, at most "
                                     f"{settings.SWX_EXPORT_MAX_ROWS} per request; ask for a shorter range")


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not math.isfinite(value) or value <= PROVIDER_FILL_CEILING:
        return None
    return value


def _attribute(value) -> str:
    if value is None or value == "":
        return STRING_FILL
    return str(value)[:ATTRIBUTE_LENGTH]


def iter_rows(ds: Dataset, start: dt.datetime, stop: dt.datetime, columns: tuple[Column, ...]):
    """Yield (time_tag, [value per column]), oldest first, from a server-side
    cursor. A value is a float or None (fill) in a double column, a str
    (STRING_FILL when missing) in a string column."""
    qs = (TimeSeriesPoint.objects.filter(product_id=ds.id, time_tag__gte=start, time_tag__lt=stop)
          .order_by("time_tag", "id").values_list("time_tag", "payload"))
    rows = qs.iterator(chunk_size=CHUNK_ROWS)
    if not ds.channel_key:
        for time_tag, payload in rows:
            yield time_tag, [_number(payload.get(c.param)) if c.type == "double" else _attribute(payload.get(c.param))
                             for c in columns]
        return
    for time_tag, group in groupby(rows, key=lambda r: r[0]):
        # One row per channel; if a channel came twice for one time_tag
        # (not seen in the stored data, D-087), the first stored one wins,
        # and the satellite is the one of the rows used.
        by_channel: dict[str, dict] = {}
        for _t, payload in group:
            by_channel.setdefault(payload.get(ds.channel_key), payload)
        first = next(iter(by_channel.values()))
        values = []
        for c in columns:
            if c.channel is not None:
                values.append(_number((by_channel.get(c.channel) or {}).get(c.param)))
            else:
                values.append(_attribute(first.get(c.param)))
        yield time_tag, values


def select_columns(ds: Dataset, raw: str | None) -> tuple[Column, ...]:
    """HAPI `parameters` subsetting (spec §3.6.1): empty = all; names must
    exist (1407) and keep the dataset's order with no duplicates (1411).
    "Time" may be named and is always included anyway."""
    if not raw:
        return ds.columns
    names = [n for n in raw.split(",") if n != "Time"]
    order = {c.name: i for i, c in enumerate(ds.columns)}
    unknown = [n for n in names if n not in order]
    if unknown:
        raise ExportError(1407, 404, "Bad request - unknown dataset parameter")
    positions = [order[n] for n in names]
    if positions != sorted(set(positions)):
        raise ExportError(1411, 400, "Bad request - out-of-order or duplicate parameters")
    return tuple(ds.columns[i] for i in positions)


def product_page_url(request, product_id: str) -> str:
    with translation.override("es"):
        return request.build_absolute_uri(reverse("web:product_detail", args=[product_id]))


def fmt_value(value) -> str:
    if value is None:
        return FILL_TEXT
    if isinstance(value, str):
        return value
    return repr(value)


RELAY_NOTE = ("Third-party data, offered for convenience and may be withdrawn: for operational real-time use, "
              "get them from the provider ({url}).")


def csv_header_lines(request, ds: Dataset, columns: tuple[Column, ...], *, retrieved_at: dt.datetime) -> list[str]:
    """The `#` comment block that opens a CSV download (§3)."""
    lines = [
        f"# {ds.title['en']} / {ds.title['es']}",
        f"# Dataset: {ds.id}",
        f"# Product page: {product_page_url(request, ds.id)}",
        f"# Source: {ds.provider}" + (f" ({ds.provider_url})" if ds.provider_url else ""),
        f"# Licence: {ds.license}",
    ]
    if ds.citation:
        lines.append(f"# Citation: {ds.citation}")
    if ds.relay:
        lines.append("# " + RELAY_NOTE.format(url=ds.provider_url or ds.provider))
    lines.append(f"# Retrieved: {fmt_time(retrieved_at)} from Clima Espacial USACH")
    lines.append("# Time: UTC, ISO 8601, start of each sample's interval")
    lines.append(f"# Fill value: {FILL_TEXT} (missing or invalid)")
    lines.append("# Columns: " + "; ".join(f"{c.name} [{c.units or ('text' if c.type == 'string' else '-')}]"
                                            for c in columns))
    return lines
