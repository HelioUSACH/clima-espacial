"""Publishing SDK for producers (docs/ARCHITECTURE.md §4.2).

A collector or service never writes to `core`'s tables directly — it calls
`publish_timeseries`, `publish_event`, `publish_forecast` or `heartbeat` —
and never reads them directly either: `read_series` is the read side. Each call re-reads the
product's manifest and keeps the `Product`/`ProductVersion` registry rows in
sync, so a manifest bump takes effect the next time a producer runs.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Iterable, Mapping

from django.db import connection, models
from django.utils import timezone

from core.manifests.loader import ManifestError, get_manifest, parse_duration
from core.models import Event, Forecast, Heartbeat, Product, ProductVersion, TimeSeriesPoint

logger = logging.getLogger(__name__)

HEARTBEAT_STATUSES = {"ok", "stale", "error", "no_data"}

# A forecast row's lead must equal one of the manifest's horizons to within
# this (D-073): absorbs a producer's rounding, never a different horizon.
HORIZON_TOLERANCE_SECONDS = 60
_ROW_META_KEYS = {"valid_at", "site"}


class PublishError(ValueError):
    """A producer tried to publish something that doesn't match its manifest."""


def _sync_product(product_id: str, manifest: dict | None = None) -> Product:
    if manifest is None:
        try:
            manifest = get_manifest(product_id)
        except ManifestError as exc:
            raise PublishError(str(exc)) from exc

    exchange = manifest.get("exchange", {})
    description = manifest.get("description", {})
    product, _created = Product.objects.update_or_create(
        id=product_id,
        defaults={
            "kind": manifest["kind"],
            "title_es": manifest["title"]["es"],
            "title_en": manifest["title"]["en"],
            "maturity": manifest["maturity"],
            "cadence_seconds": parse_duration(manifest["cadence"]),
            "stale_after_seconds": parse_duration(manifest["stale_after"]),
            "producer_type": manifest["producer"]["type"],
            "producer_repo": manifest["producer"].get("repo", ""),
            "source_provider": manifest["source"]["provider"],
            "source_license": manifest["source"].get("license", ""),
            "exchange_hapi": exchange.get("hapi", False),
            "exchange_feed": exchange.get("feed", False),
            "model_card_path": manifest.get("model_card", ""),
            "manifest_path": manifest["_path"],
            "module": manifest["_module"],
            "current_version": manifest["version"],
            "description_es": description.get("es", ""),
            "description_en": description.get("en", ""),
            "facts": manifest.get("facts", {}),
        },
    )
    ProductVersion.objects.update_or_create(
        product=product,
        version=manifest["version"],
        defaults={"parameters": manifest.get("parameters", []), "manifest": manifest},
    )
    return product


def _as_aware_datetime(value) -> dt.datetime:
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timezone.is_naive(value):
        value = timezone.make_aware(value, dt.UTC)
    return value


# The monthly-partitioned parents (D-032, D-072); _ensure_partition only
# ever builds SQL from these names, never from caller input.
PARTITIONED_TABLES = frozenset({"core_timeseries", "core_forecast"})


def _ensure_partition(parent: str, when: dt.datetime) -> None:
    """Idempotent monthly partition of `parent` (D-032) covering `when`.
    Called before every insert instead of by a separate scheduled job, so a
    module never needs its own "make sure the table exists" step."""
    if parent not in PARTITIONED_TABLES:
        raise ValueError(f"{parent!r} is not a partitioned table")
    month_start = when.astimezone(dt.UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    next_month = (month_start + dt.timedelta(days=32)).replace(day=1)
    table = f"{parent}_y{month_start:%Y}m{month_start:%m}"
    with connection.cursor() as cursor:
        cursor.execute(
            f'CREATE TABLE IF NOT EXISTS "{table}" '  # noqa: S608 (both names are our own, not user input)
            f"PARTITION OF {parent} FOR VALUES FROM (%s) TO (%s)",
            [month_start, next_month],
        )


def publish_timeseries(
    product_id: str,
    rows: Iterable[Mapping],
    *,
    quality: dict | None = None,
    provenance: dict | None = None,
) -> int:
    """`rows`: an iterable of plain dicts, each with a `time_tag` (ISO string
    or datetime), an optional `source`, and one key per manifest parameter.
    No pandas dependency (deviates from the original sketch in
    03-ARCHITECTURE-PROPOSAL.md §4.2 — see docs/DECISIONS.md D-048).

    Rows at or before the product's latest already-stored `time_tag` are
    skipped, so a collector that re-fetches a rolling window (e.g. SWPC's
    "last N days" files) on every run can call this unconditionally without
    piling up duplicates. This is cheap re-run idempotency, not a backfill
    tool: it can't be used to fill an older gap in an otherwise-current
    series (see docs/ARCHITECTURE.md §4.2)."""
    product = _sync_product(product_id)
    rows = list(rows)
    if not rows:
        # An adapter that always comes back empty is a bug, not a quiet
        # source: surface it as "no_data" rather than rubber-stamping "ok".
        # (A non-empty fetch with nothing *new* — the common steady-state
        # case for a slow-cadence product polled often — is "ok": see below.)
        heartbeat(product_id, "no_data", detail="collector ran but returned 0 rows")
        return 0

    latest = TimeSeriesPoint.objects.filter(product=product).aggregate(models.Max("time_tag"))["time_tag__max"]
    objects = []
    for row in rows:
        row = dict(row)
        try:
            time_tag = row.pop("time_tag")
        except KeyError:
            raise PublishError(f"{product_id}: row missing required 'time_tag': {row!r}") from None
        time_tag = _as_aware_datetime(time_tag)
        if latest is not None and time_tag <= latest:
            continue
        source = row.pop("source", "") or ""
        _ensure_partition("core_timeseries", time_tag)
        objects.append(
            TimeSeriesPoint(
                product=product,
                time_tag=time_tag,
                source=source,
                payload=row,
                quality=quality,
                provenance=provenance,
            )
        )
    if objects:
        TimeSeriesPoint.objects.bulk_create(objects)
        heartbeat(product_id, "ok", detail=f"published {len(objects)} new point(s) of {len(rows)} fetched")
    else:
        heartbeat(product_id, "ok", detail="no new points since last publish (source unchanged)")
    return len(objects)


def publish_event(
    product_id: str,
    payload: dict,
    *,
    valid_from,
    valid_to=None,
    issued=None,
    quality: dict | None = None,
    provenance: dict | None = None,
) -> Event:
    product = _sync_product(product_id)
    event = Event.objects.create(
        product=product,
        issued=_as_aware_datetime(issued) if issued else timezone.now(),
        valid_from=_as_aware_datetime(valid_from),
        valid_to=_as_aware_datetime(valid_to) if valid_to else None,
        payload=payload,
        quality=quality,
        provenance=provenance,
    )
    heartbeat(product_id, "ok", detail="event published")
    return event


def heartbeat(product_id: str, status: str, *, detail: str = "") -> Heartbeat:
    if status not in HEARTBEAT_STATUSES:
        raise PublishError(f"invalid heartbeat status {status!r}, expected one of {sorted(HEARTBEAT_STATUSES)}")
    return _set_heartbeat(_sync_product(product_id), status, detail)


def _set_heartbeat(product: Product, status: str, detail: str) -> Heartbeat:
    # For a caller that has just synced the product itself.
    hb, _created = Heartbeat.objects.update_or_create(
        product=product,
        defaults={"status": status, "last_seen_at": timezone.now(), "detail": detail},
    )
    return hb


def read_series(product_id: str, field: str, since, until=None, *, site: str = "") -> list[tuple[dt.datetime, float]]:
    """The read side of the SDK: `[(time_tag, value), ...]` of one numeric
    field, oldest first, `since <= time_tag` (and `< until` if given).
    Non-numeric values (a provider's "" or null) are left out. A module
    reads observations through this, never by querying `core_timeseries`.

    `site`: for a target with a site dimension (stations, grid cells), only
    the rows whose payload carries that `site` code (the convention a
    site-dimensioned observed product follows, D-073); "" = every row."""
    qs = TimeSeriesPoint.objects.filter(product_id=product_id, time_tag__gte=_as_aware_datetime(since))
    if until is not None:
        qs = qs.filter(time_tag__lt=_as_aware_datetime(until))
    if site:
        qs = qs.filter(payload__site=site)
    out = []
    for time_tag, value in qs.order_by("time_tag").values_list("time_tag", f"payload__{field}"):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append((time_tag, float(value)))
    return out


def floor_time(when: dt.datetime, seconds: int) -> dt.datetime:
    """Floor an aware datetime to a multiple of `seconds` since the UNIX epoch."""
    epoch = int(when.timestamp())
    return dt.datetime.fromtimestamp(epoch - epoch % seconds, tz=dt.UTC)


def floor_issue_time(product_id: str, when: dt.datetime | None = None) -> dt.datetime:
    """The `issued_at` a forecast producer must use (D-073): `when` (default
    now) floored to the manifest's `cadence`. A deterministic issue time is
    what makes a re-run collide with the first run instead of adding a
    near-duplicate issue a few seconds later."""
    cadence = parse_duration(get_manifest(product_id)["cadence"])
    return floor_time(_strict_aware(when or timezone.now(), "when"), cadence)


def _strict_aware(value, what: str) -> dt.datetime:
    # Unlike publish_timeseries, a forecast refuses to guess a timezone: an
    # issue/valid time off by the producer's UTC offset would verify against
    # the wrong observation and nobody would notice (D-073).
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, dt.datetime):
        raise PublishError(f"{what} must be a datetime or ISO 8601 string, got {value!r}")
    if timezone.is_naive(value):
        raise PublishError(f"{what} {value.isoformat()} is naive: forecasts need an explicit UTC offset")
    return value.astimezone(dt.UTC)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _forecast_values(product_id, row, field, uncertainty, i) -> dict:
    extra = set(row) - _ROW_META_KEYS - {field}
    if extra:
        raise PublishError(f"{product_id}: row {i} has field(s) {sorted(extra)}; this model forecasts only {field!r}")
    entry = row.get(field)
    if not isinstance(entry, Mapping) or not _is_number(entry.get("value")):
        raise PublishError(f"{product_id}: row {i} needs {field!r}: {{'value': <number>, ...}}, got {entry!r}")
    value, lower, upper = entry["value"], entry.get("lower"), entry.get("upper")
    unknown = set(entry) - {"value", "lower", "upper"}
    if unknown:
        raise PublishError(f"{product_id}: row {i} {field!r} has unknown key(s) {sorted(unknown)}")
    if uncertainty == "bounds":
        if not (_is_number(lower) and _is_number(upper)):
            raise PublishError(f"{product_id}: row {i} needs numeric 'lower' and 'upper' (uncertainty: bounds)")
        if lower > value or value > upper:
            raise PublishError(f"{product_id}: row {i} must satisfy lower <= value <= upper, got {lower}, {value}, {upper}")
    elif lower is not None or upper is not None:
        raise PublishError(f"{product_id}: row {i} has bounds but the manifest declares uncertainty: none")
    return {field: {"value": float(value),
                    "lower": float(lower) if lower is not None else None,
                    "upper": float(upper) if upper is not None else None}}


def publish_forecast(
    product_id: str,
    *,
    issued_at,
    model_version: str,
    rows: Iterable[Mapping],
    provenance: Mapping,
) -> int:
    """Publish one issue of a `kind: forecast` product (D-073). Returns the
    number of rows inserted.

    `rows`: one dict per (valid time, site),
    `{"valid_at": <aware datetime | ISO>, "site": "", "<target field>":
    {"value": v, "lower": lo, "upper": hi}}` (`site` may be omitted for a
    global index; `lower`/`upper` only with `uncertainty: bounds`).

    - `issued_at` must be aware and floored to the manifest `cadence`
      (`floor_issue_time`), so it is deterministic.
    - An issue is atomic and write-once per `model_version`: if any row of
      (product, issued_at, model_version) is already stored, nothing is
      inserted and 0 is returned; if the new rows differ from the stored
      ones, that is logged as a warning (a correction ships as a new
      `model_version`, never as a silent overwrite). The insert itself is
      `ON CONFLICT DO NOTHING`, so a concurrent re-run can't duplicate.
    - Raises `PublishError` (and writes nothing) on: a lead that isn't a
      declared horizon to within 60 s, a field other than the target field,
      lower > value or value > upper, bounds missing under `bounds`, a site
      that doesn't match the manifest's `sites`, naive datetimes, a reserved
      `uncertainty: quantiles`, or a duplicate (valid_at, site) in the issue.
    - Heartbeat: `ok` on publish (a no-op re-run included), `no_data` on
      zero rows — same as `publish_timeseries`.
    """
    try:
        manifest = get_manifest(product_id)
    except ManifestError as exc:
        raise PublishError(str(exc)) from exc
    if manifest["kind"] != "forecast":
        raise PublishError(f"{product_id} is kind {manifest['kind']!r}, not 'forecast'")
    spec = manifest["forecast"]
    uncertainty = spec["uncertainty"]["type"]
    if uncertainty == "quantiles":
        raise PublishError(f"{product_id}: uncertainty type 'quantiles' is reserved and not implemented in 0.4")
    field = spec["target"]["field"]
    horizons = [parse_duration(h) for h in spec["horizons"]]
    cadence = parse_duration(manifest["cadence"])

    issued_at = _strict_aware(issued_at, "issued_at")
    if int(issued_at.timestamp()) % cadence or issued_at.microsecond:
        raise PublishError(
            f"{product_id}: issued_at {issued_at.isoformat()} is not on a {manifest['cadence']} boundary; "
            "floor it with swxkit.floor_issue_time() so a re-run is idempotent"
        )
    if not isinstance(model_version, str) or not model_version.strip():
        raise PublishError(f"{product_id}: model_version must be a non-empty string")
    if not isinstance(provenance, Mapping):
        raise PublishError(f"{product_id}: provenance must be a mapping")

    product = _sync_product(product_id, manifest)
    rows = list(rows)
    if not rows:
        _set_heartbeat(product, "no_data", "producer ran but issued 0 rows")
        return 0

    prepared, seen = [], set()
    for i, row in enumerate(rows):
        if not isinstance(row, Mapping) or "valid_at" not in row:
            raise PublishError(f"{product_id}: row {i} is missing 'valid_at': {row!r}")
        valid_at = _strict_aware(row["valid_at"], f"row {i} valid_at")
        lead = int((valid_at - issued_at).total_seconds())
        if not any(abs(lead - h) <= HORIZON_TOLERANCE_SECONDS for h in horizons):
            raise PublishError(
                f"{product_id}: row {i} lead {lead}s is not a declared horizon {spec['horizons']} (±{HORIZON_TOLERANCE_SECONDS}s)"
            )
        site = row.get("site", "") or ""
        if not isinstance(site, str):
            raise PublishError(f"{product_id}: row {i} site must be a string")
        if spec["sites"] and not site:
            raise PublishError(f"{product_id}: row {i} needs a site code (manifest sites: true)")
        if not spec["sites"] and site:
            raise PublishError(f"{product_id}: row {i} has site {site!r} but the manifest declares sites: false")
        if (valid_at, site) in seen:
            raise PublishError(f"{product_id}: row {i} repeats valid_at {valid_at.isoformat()} site {site!r}")
        seen.add((valid_at, site))
        prepared.append((valid_at, lead, site, _forecast_values(product_id, row, field, uncertainty, i)))

    stored = {
        (f.valid_at, f.site): f.values
        for f in Forecast.objects.filter(product=product, issued_at=issued_at, model_version=model_version)
    }
    if stored:
        new = {(valid_at, site): values for valid_at, _lead, site, values in prepared}
        if new != stored:
            logger.warning(
                "%s: issue %s model_version %s is already stored and the new rows differ; ignored "
                "(publish a correction as a new model_version)", product_id, issued_at.isoformat(), model_version,
            )
        _set_heartbeat(product, "ok", f"issue {issued_at:%Y-%m-%dT%H:%MZ} already published")
        return 0

    _ensure_partition("core_forecast", issued_at)
    provenance_json = json.dumps(dict(provenance))
    params, tuples = [], []
    for valid_at, lead, site, values in prepared:
        tuples.append("(%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)")
        params += [product_id, model_version, issued_at, valid_at, lead, site, json.dumps(values), provenance_json]
    with connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO core_forecast (product_id, model_version, issued_at, valid_at, lead_seconds, site, "values", provenance) '
            "VALUES " + ", ".join(tuples) + " ON CONFLICT DO NOTHING",
            params,
        )
        inserted = cursor.rowcount
    _set_heartbeat(product, "ok", f"issue {issued_at:%Y-%m-%dT%H:%MZ}: {inserted} row(s)")
    return inserted
