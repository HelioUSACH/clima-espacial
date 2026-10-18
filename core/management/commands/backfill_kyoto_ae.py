"""`manage.py backfill_kyoto_ae --since 2026-01-01`: one-off, after deploying
0.4.2 (deploy/README.md). Pulls Kyoto's `min_ae` (1-minute, best available)
into `kyoto.ae`, a month at a time (about 1440 rows a day). Revisable like
backfill_kyoto_dst, so it also repairs."""

import datetime as dt

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from modules.kyoto import client, collectors


class Command(BaseCommand):
    help = "Backfill kyoto.ae from Kyoto's HAPI min_ae (one-off; also a repair)."

    def add_arguments(self, parser):
        parser.add_argument("--since", required=True, help="UTC date, e.g. 2026-01-01")

    def handle(self, *args, **options):
        try:
            since = dt.datetime.fromisoformat(options["since"]).replace(tzinfo=dt.UTC)
        except ValueError:
            raise CommandError("--since must be a date like 2026-01-01") from None
        try:
            n = collectors.backfill(collectors.AE_ID, collectors.AE_DATASET, since, timezone.now(),
                                    dt.timedelta(days=30), write=self.stdout.write)
        except client.HapiError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"{collectors.AE_ID}: {n} row(s) new or revised"))
