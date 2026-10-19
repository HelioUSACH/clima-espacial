"""Usage by sector for the project's reporting (D-086): CSV on stdout.

    manage.py api_usage_report --from 2026-10-01 --to 2026-12-31 [--by month|day]

One row per (period, sector) with anything to report: active keys (keys
that made at least one request in the period), requests, rows, and new
registrations. Sectors are the registration form's codes plus `service`
(service keys), `anonymous` (keyless preview requests) and `unregistered`
(a user key with no registration). No names or emails, ever.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from api.models import ApiUsageDay
from web.models import RegistrationRequest

FIELDS = ("period", "sector", "active_keys", "requests", "rows", "new_registrations")
EXTRA_SECTORS = ("service", "unregistered", "anonymous")


def _date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise CommandError(f"invalid date {value!r}, expected YYYY-MM-DD") from None


def _period(day: dt.date, by: str) -> str:
    return day.isoformat() if by == "day" else f"{day:%Y-%m}"


def _sector(kind, sector) -> str:
    if kind is None:
        return "anonymous"
    if kind == "service":
        return "service"
    return sector or "unregistered"


def usage_report(start: dt.date, stop: dt.date, by: str = "month") -> list[dict]:
    rows = defaultdict(lambda: {"keys": set(), "requests": 0, "rows": 0, "new_registrations": 0})
    usage = (ApiUsageDay.objects.filter(day__gte=start, day__lte=stop)
             .values_list("day", "key_id", "key__kind", "key__registration__sector", "requests", "rows"))
    for day, key_id, kind, sector, requests, n_rows in usage:
        entry = rows[(_period(day, by), _sector(kind, sector))]
        entry["requests"] += requests
        entry["rows"] += n_rows
        if key_id is not None and requests:
            entry["keys"].add(key_id)
    registrations = RegistrationRequest.objects.filter(
        created_at__gte=dt.datetime.combine(start, dt.time(), tzinfo=dt.UTC),
        created_at__lt=dt.datetime.combine(stop + dt.timedelta(days=1), dt.time(), tzinfo=dt.UTC),
    ).values_list("created_at", "sector")
    for created_at, sector in registrations:
        rows[(_period(created_at.astimezone(dt.UTC).date(), by), sector)]["new_registrations"] += 1

    order = [code for code, _label in RegistrationRequest.SECTOR_CHOICES] + list(EXTRA_SECTORS)
    rank = {s: i for i, s in enumerate(order)}
    out = []
    for (period, sector) in sorted(rows, key=lambda k: (k[0], rank.get(k[1], len(rank)), k[1])):
        entry = rows[(period, sector)]
        out.append({"period": period, "sector": sector, "active_keys": len(entry["keys"]),
                    "requests": entry["requests"], "rows": entry["rows"],
                    "new_registrations": entry["new_registrations"]})
    return out


class Command(BaseCommand):
    help = "API usage and new registrations by sector, as CSV on stdout (no personal data)."

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="start", required=True, help="first day, YYYY-MM-DD (UTC)")
        parser.add_argument("--to", dest="stop", required=True, help="last day, YYYY-MM-DD (UTC), inclusive")
        parser.add_argument("--by", choices=("month", "day"), default="month")

    def handle(self, *args, start, stop, by, **options):
        start, stop = _date(start), _date(stop)
        if stop < start:
            raise CommandError("--to is before --from")
        writer = csv.DictWriter(self.stdout, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in usage_report(start, stop, by):
            writer.writerow(row)
