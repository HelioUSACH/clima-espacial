"""`manage.py backfill_kyoto_dst --days 30`: one-off, after deploying 0.4.2
(deploy/README.md). Pulls Kyoto's realtime Dst for the last N days into
`kyoto.dst`. The product is revisable (D-080) and every chunk is published
with `revise_since`, so re-running it also repairs stored hours Kyoto has
since revised."""

import datetime as dt

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from modules.kyoto import client, collectors


class Command(BaseCommand):
    help = "Backfill kyoto.dst from Kyoto's HAPI hour_dst_realtime (one-off; also a repair)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)

    def handle(self, *args, **options):
        if options["days"] < 1:
            raise CommandError("--days must be at least 1")
        now = timezone.now()
        try:
            n = collectors.backfill(collectors.DST_ID, collectors.DST_DATASET, now - dt.timedelta(days=options["days"]),
                                    now, dt.timedelta(days=10), write=self.stdout.write)
        except client.HapiError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"{collectors.DST_ID}: {n} row(s) new or revised"))
