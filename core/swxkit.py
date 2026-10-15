"""Publishing SDK for producers (docs/ARCHITECTURE.md §4.2).

A collector or service never writes to `core`'s tables directly — it calls
`publish_timeseries`, `publish_event` or `heartbeat`. Each call re-reads the
product's manifest and keeps the `Product`/`ProductVersion` registry rows in
sync, so a manifest bump takes effect the next time a producer runs.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping

from django.db import connection, models
from django.utils import timezone

from core.manifests.loader import ManifestError, get_manifest, parse_duration
from core.models import Event, Heartbeat, Product, ProductVersion, TimeSeriesPoint

HEARTBEAT_STATUSES = {"ok", "stale", "error", "no_data"}


class PublishError(ValueError):
    """A producer tried to publish something that doesn't match its manifest."""


def _sync_product(product_id: str) -> Product:
    try:
        manifest = get_manifest(product_id)
    except ManifestError as exc:
        raise PublishError(str(exc)) from exc

    exchange = manifest.get("exchange", {})
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


def _ensure_partition(time_tag: dt.datetime) -> None:
    """Idempotent monthly partition for `core_timeseries` (D-032). Called
    before every insert instead of by a separate scheduled job, so a module
    never needs its own "make sure the table exists" step."""
    month_start = time_tag.astimezone(dt.UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    next_month = (month_start + dt.timedelta(days=32)).replace(day=1)
    table = f"core_timeseries_y{month_start:%Y}m{month_start:%m}"
    with connection.cursor() as cursor:
        cursor.execute(
            f'CREATE TABLE IF NOT EXISTS "{table}" '  # noqa: S608 (table name is our own, not user input)
            f"PARTITION OF core_timeseries FOR VALUES FROM (%s) TO (%s)",
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
        _ensure_partition(time_tag)
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
    product = _sync_product(product_id)
    hb, _created = Heartbeat.objects.update_or_create(
        product=product,
        defaults={"status": status, "last_seen_at": timezone.now(), "detail": detail},
    )
    return hb
