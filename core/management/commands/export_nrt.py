"""`manage.py export_nrt` (D-091): the stored NRT record, as files, for the
off-machine archive.

For every product with stored rows (Kyoto and retired products included:
the backup area is private and the files are for internal use), one file
per product and UTC day:

    <SWX_NRT_EXPORT_DIR>/<product>/<yyyy>/<product>_<yyyymmdd>.jsonl.gz

gzipped JSON lines, one stored row per line, exactly as stored:

- time series: time_tag, source, payload, quality, provenance, inserted_at,
  revised_at (the day is time_tag's UTC date);
- events: issued, valid_from, valid_to, payload, quality, provenance,
  created_at (by issued);
- forecasts: model_version, issued_at, valid_at, lead_seconds, site, values,
  provenance, inserted_at (by issued_at: an issue's rows stay in one file).

Each run re-writes every day that gained or changed rows (inserted_at,
revised_at or created_at later than the marker the last successful run
left), so late data (Kyoto AE arrives weeks late), revisions (D-080) and
backfills reach the archive; the first run writes every day there is. The
marker is the run's start minus an hour, so a row stamped just before the
run but committed after it is still caught next time.

Output is deterministic (rows in stored order, keys sorted, no gzip
timestamp or name), so re-exporting an unchanged day gives the same bytes;
such a file is left untouched. A file is written to `<name>.tmp` and
renamed, and the backup pull skips `*.tmp`. Files are 0640: the directory
belongs to the `swx-backup` group (setgid, deploy/systemd/swx-export-nrt.service),
never world-readable. After exporting, files whose last write is older than
SWX_NRT_KEEP_DAYS days are deleted here; the backup host keeps everything.
"""

from __future__ import annotations

import datetime as dt
import filecmp
import gzip
import json
import os
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from core.models import Event, Forecast, TimeSeriesPoint

MARKER_NAME = ".export_nrt.json"
OVERLAP = dt.timedelta(hours=1)
FILE_MODE = 0o640
DIR_MODE = 0o750
SUFFIX = ".jsonl.gz"
TMP_SUFFIX = ".tmp"


@dataclass(frozen=True)
class Table:
    model: type
    day_field: str  # rows are filed by this field's UTC date
    changed_fields: tuple[str, ...]  # a row newer than the marker in any of these re-exports its day
    order_by: tuple[str, ...]
    fields: tuple[str, ...]  # what each line carries, under these names


TABLES = (
    Table(TimeSeriesPoint, "time_tag", ("inserted_at", "revised_at"), ("time_tag", "id"),
          ("time_tag", "source", "payload", "quality", "provenance", "inserted_at", "revised_at")),
    Table(Event, "issued", ("created_at",), ("issued", "id"),
          ("issued", "valid_from", "valid_to", "payload", "quality", "provenance", "created_at")),
    Table(Forecast, "issued_at", ("inserted_at",), ("issued_at", "model_version", "valid_at", "site", "id"),
          ("model_version", "issued_at", "valid_at", "lead_seconds", "site", "values", "provenance", "inserted_at")),
)


def _time(value: dt.datetime | None) -> str | None:
    """ISO 8601 UTC with Z; microseconds only when there are any."""
    if value is None:
        return None
    value = value.astimezone(dt.UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ" if value.microsecond else "%Y-%m-%dT%H:%M:%SZ")


def _line(record: dict) -> bytes:
    record = {k: _time(v) if isinstance(v, dt.datetime) else v for k, v in record.items()}
    return (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def day_path(root: Path, product_id: str, day: dt.date) -> Path:
    return root / product_id / f"{day:%Y}" / f"{product_id}_{day:%Y%m%d}{SUFFIX}"


def _mkdirs(root: Path, path: Path) -> None:
    """Create each missing level below `root` as 0750 (Path.mkdir(parents=True)
    would give the intermediate ones the default mode). A new directory
    inherits the setgid bit, and so the group, from its parent."""
    for level in reversed([path, *path.parents]):
        if level == root or root not in level.parents:
            continue
        if not level.is_dir():
            level.mkdir(mode=DIR_MODE)


def changed_days(table: Table, since: dt.datetime | None) -> set[tuple[str, dt.date]]:
    """(product, UTC day) pairs with a row inserted or changed after `since`
    (every pair when `since` is None)."""
    qs = table.model.objects.all()
    if since is not None:
        cond = Q()
        for name in table.changed_fields:
            cond |= Q(**{f"{name}__gt": since})
        qs = qs.filter(cond)
    pairs = (qs.order_by().annotate(day=TruncDate(table.day_field, tzinfo=dt.UTC))
             .values_list("product_id", "day").distinct())
    return set(pairs)


def write_day(root: Path, table: Table, product_id: str, day: dt.date) -> str:
    """Write one day's file; "written", "unchanged" or "empty"."""
    start = dt.datetime.combine(day, dt.time(), tzinfo=dt.UTC)
    rows = (table.model.objects
            .filter(product_id=product_id, **{f"{table.day_field}__gte": start,
                                               f"{table.day_field}__lt": start + dt.timedelta(days=1)})
            .order_by(*table.order_by).values_list(*table.fields))
    path = day_path(root, product_id, day)
    _mkdirs(root, path.parent)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    n = 0
    with open(tmp, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        for values in rows.iterator(chunk_size=2000):
            gz.write(_line(dict(zip(table.fields, values, strict=True))))
            n += 1
    if n == 0:
        tmp.unlink()
        return "empty"
    if path.exists() and filecmp.cmp(tmp, path, shallow=False):
        tmp.unlink()
        return "unchanged"
    os.chmod(tmp, FILE_MODE)
    os.replace(tmp, path)
    return "written"


def prune(root: Path, keep_days: int, now: dt.datetime) -> int:
    """Delete day files last written more than `keep_days` ago (0: keep
    all), leftover temp files older than a day, then empty directories.
    Never touches the marker. Returns the number of day files deleted."""
    deleted = 0
    if not root.is_dir():
        return 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        age = now.timestamp() - path.stat().st_mtime
        if path.name.endswith(SUFFIX) and keep_days and age > keep_days * 86400:
            path.unlink()
            deleted += 1
        elif path.name.endswith(TMP_SUFFIX) and age > 86400:
            path.unlink()
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(path.iterdir()):
            path.rmdir()
    return deleted


def read_marker(root: Path) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(json.loads((root / MARKER_NAME).read_text())["marker"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_marker(root: Path, marker: dt.datetime, finished: dt.datetime, written: int) -> None:
    path = root / MARKER_NAME
    tmp = path.with_name(path.name + TMP_SUFFIX)
    tmp.write_text(json.dumps({"marker": marker.isoformat(), "finished_at": finished.isoformat(),
                               "files_written": written}, indent=1) + "\n")
    os.chmod(tmp, FILE_MODE)
    os.replace(tmp, path)


def export(root: Path, *, keep_days: int, full: bool = False, now: dt.datetime | None = None) -> dict:
    """One run. The marker moves only if every day was written."""
    started = now or timezone.now()
    root.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    since = None if full else read_marker(root)
    counts = {"written": 0, "unchanged": 0, "empty": 0}
    per_product: dict[str, int] = {}
    for table in TABLES:
        for product_id, day in sorted(changed_days(table, since)):
            outcome = write_day(root, table, product_id, day)
            counts[outcome] += 1
            if outcome == "written":
                per_product[product_id] = per_product.get(product_id, 0) + 1
    write_marker(root, started - OVERLAP, timezone.now(), counts["written"])
    counts["pruned"] = prune(root, keep_days, started)
    return {"since": since, "per_product": per_product, **counts}


class Command(BaseCommand):
    help = ("Write the stored rows of every product, per UTC day, as gzipped JSON lines under "
            "SWX_NRT_EXPORT_DIR, re-writing each day that changed since the last run (D-091).")

    def add_arguments(self, parser):
        parser.add_argument("--dir", type=Path, default=None, help="Output directory (default SWX_NRT_EXPORT_DIR).")
        parser.add_argument("--keep-days", type=int, default=None,
                            help="Delete files last written more than N days ago (default SWX_NRT_KEEP_DAYS; 0 keeps all).")
        parser.add_argument("--full", action="store_true", help="Ignore the marker: re-export every day.")

    def handle(self, *args, **options):
        root = options["dir"] or Path(settings.SWX_NRT_EXPORT_DIR)
        keep = settings.SWX_NRT_KEEP_DAYS if options["keep_days"] is None else options["keep_days"]
        result = export(root, keep_days=keep, full=options["full"])
        since = result["since"].isoformat() if result["since"] else "the beginning (first run)"
        self.stdout.write(f"export_nrt: days changed since {since}")
        for product_id, n in sorted(result["per_product"].items()):
            self.stdout.write(f"  {product_id}: {n} day file(s) written")
        self.stdout.write(self.style.SUCCESS(
            f"export_nrt: {result['written']} written, {result['unchanged']} unchanged, "
            f"{result['pruned']} older than {keep} days deleted, in {root}"))
