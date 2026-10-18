"""Synthetic, deterministic dev data for every home-page product (D-055):
`manage.py seed_dev_data --days 7`. Dev/CI only — it refuses to run outside
SWX_AMBIENTE=dev, since it deletes each seeded product's stored rows first.

Goes through `core.swxkit` like any producer (never writes the product
tables directly). The shapes match what `modules/swpc/adapters.py` emits
from the real recorded fixtures, including the awkward parts the charts
must cope with: NOAA's `-9999` fill value, a change of active L1 spacecraft,
a real 40-minute outage, sparse Kp (3 h) and Dst (1 h, its last hours from
NOAA's mirror as when Kyoto's route fails), energy channels interleaved one
row per channel per timestamp, and AE that ends two weeks before now, like
Kyoto's delayed HAPI copy (D-082)."""

import datetime as dt
import importlib
import math
import random

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core import swxkit
from core.management.commands.verify_forecasts import verify_product
from core.manifests.loader import get_manifest
from core.models import Event, Forecast, Product, TimeSeriesPoint

FILL = -9999
AE_DELAY = dt.timedelta(days=13)
# Forecast products replayed hour by hour over the seeded Kp (v0.4.0 §7).
FORECAST_PRODUCTS = ("usach.kp.persistence",)
PROTON_CHANNELS = {
    ">=1 MeV": 2.0, ">=5 MeV": 0.6, ">=10 MeV": 0.35, ">=30 MeV": 0.2,
    ">=50 MeV": 0.15, ">=60 MeV": 0.13, ">=100 MeV": 0.09, ">=500 MeV": 0.03,
}


def _times(start, end, step_seconds):
    t = start
    while t <= end:
        yield t
        t += dt.timedelta(seconds=step_seconds)


class Command(BaseCommand):
    help = "Replace the stored rows of the NOAA and Kyoto products with N days of synthetic data (dev/CI only)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=float, default=7.0)
        parser.add_argument("--seed", type=int, default=20261016)

    def handle(self, *args, **options):
        if settings.SWX_AMBIENTE != "dev":
            raise CommandError("seed_dev_data deletes stored rows: it only runs with SWX_AMBIENTE=dev.")
        rnd = random.Random(options["seed"])
        end = timezone.now().replace(second=0, microsecond=0)
        start = end - dt.timedelta(days=options["days"])
        switch_at = start + (end - start) * 0.6  # active L1 spacecraft changes here
        outage = (start + (end - start) * 0.3, start + (end - start) * 0.3 + dt.timedelta(minutes=40))

        def phase(t):
            return (t - start).total_seconds() / 86400.0

        mag, wind = [], []
        for t in _times(start, end, 60):
            if outage[0] <= t < outage[1]:
                continue
            d = phase(t)
            src = "ACE" if t >= switch_at else "SOLAR1"
            bz = 4 * math.sin(2 * math.pi * d / 1.3) + rnd.gauss(0, 1.2)
            bx, by = 3 * math.cos(d * 4) + rnd.gauss(0, 1), 3 * math.sin(d * 3) + rnd.gauss(0, 1)
            bt = math.sqrt(bx * bx + by * by + bz * bz) + abs(rnd.gauss(0, 0.3))
            speed = 450 + 120 * math.sin(2 * math.pi * d / 3.1) + rnd.gauss(0, 12)
            density = max(0.3, 6 + 4 * math.sin(2 * math.pi * d / 2.2 + 1) + rnd.gauss(0, 1))
            temperature = max(1e4, 1.2e5 * math.exp(0.6 * math.sin(2 * math.pi * d / 3.1)) * rnd.lognormvariate(0, 0.15))
            mag_row = {"time_tag": t, "source": src, "bt": round(bt, 2), "bx_gsm": round(bx, 2),
                       "by_gsm": round(by, 2), "bz_gsm": round(bz, 2)}
            wind_row = {"time_tag": t, "source": src, "proton_speed": round(speed, 1),
                        "proton_density": round(density, 2), "proton_temperature": int(temperature)}
            if rnd.random() < 0.004:  # NOAA's fill value, as stored by the real adapters
                mag_row["bz_gsm"] = FILL
                wind_row["proton_speed"] = FILL
            mag.append(mag_row)
            wind.append(wind_row)

        kp = []
        kp_start = start.replace(hour=start.hour - start.hour % 3, minute=0)
        for t in _times(kp_start, end, 3 * 3600):
            value = max(0.0, min(9.0, 2.5 + 2.5 * math.sin(2 * math.pi * phase(t) / 2.5) + rnd.gauss(0, 0.6)))
            kp.append({"time_tag": t, "kp": round(value * 3) / 3, "a_running": int(value * 5), "station_count": 8})

        dst = []
        for t in _times(start.replace(minute=0), end, 3600):
            fallback = t > end - dt.timedelta(hours=3)
            dst.append({"time_tag": t, "dst": int(-15 - 40 * max(0, math.sin(2 * math.pi * phase(t) / 2.5)) + rnd.gauss(0, 4)),
                        "version": -1 if fallback else 0, "source": "NOAA-MIRROR" if fallback else "KYOTO-HAPI"})

        # AE, 1-minute, ending AE_DELAY before now: a few substorms a day.
        ae = []
        ae_end = end - AE_DELAY
        ae_start = ae_end - (end - start)
        substorms = [ae_start + dt.timedelta(hours=rnd.uniform(0, options["days"] * 24)) for _ in range(int(options["days"] * 3))]
        for t in _times(ae_start, ae_end, 60):
            drive = sum(math.exp(-((t - s).total_seconds() / 3600 - 0.5) ** 2 / 0.15)
                        for s in substorms if -2 * 3600 < (t - s).total_seconds() < 4 * 3600)
            au = int(40 + 60 * drive + rnd.gauss(0, 8))
            al = int(-30 - 420 * drive + rnd.gauss(0, 12))
            ae.append({"time_tag": t, "source": "KYOTO-HAPI", "ae": au - al, "al": al, "au": au,
                       "ao": (au + al) // 2, "version": 0 if t > ae_end - dt.timedelta(days=2) else 10})

        xray, protons = [], []
        # (peak time, peak long-channel flux): one B/C/M/X-class mix
        flares = [(start + dt.timedelta(hours=rnd.uniform(0, options["days"] * 24)), peak)
                  for peak in (8e-7, 2e-6, 5e-6, 1.5e-5, 4e-5, 1.2e-4)]
        for t in _times(start, end, 60):
            background = 2e-7 * math.exp(0.8 * math.sin(2 * math.pi * phase(t) / 4))
            for peak_time, peak in flares:
                minutes = (t - peak_time).total_seconds() / 60
                if -10 <= minutes < 0:
                    background += peak * (minutes + 10) / 10  # impulsive rise
                elif 0 <= minutes < 180:
                    background += peak * math.exp(-minutes / 20)  # gradual decay
            long_flux = background * rnd.lognormvariate(0, 0.05)
            xray.append({"time_tag": t, "energy": "0.1-0.8nm", "flux": long_flux, "satellite": 18})
            xray.append({"time_tag": t, "energy": "0.05-0.4nm", "flux": long_flux / 12, "satellite": 18})
            if t.minute % 5 == 0:
                for energy, base in PROTON_CHANNELS.items():
                    flux = base * math.exp(0.3 * math.sin(2 * math.pi * phase(t) / 5)) * rnd.lognormvariate(0, 0.08)
                    protons.append({"time_tag": t, "energy": energy, "flux": flux, "satellite": 18})

        for product_id, rows in (
            ("noaa.rtsw.mag", mag), ("noaa.rtsw.wind", wind), ("noaa.kp", kp), ("kyoto.dst", dst),
            ("noaa.goes.xray", xray), ("noaa.goes.protons", protons), ("kyoto.ae", ae),
        ):
            TimeSeriesPoint.objects.filter(product_id=product_id).delete()
            n = swxkit.publish_timeseries(product_id, rows)
            self.stdout.write(f"{product_id}: {n} rows")

        Event.objects.filter(product_id="noaa.scales").delete()
        today = {"DateStamp": f"{end:%Y-%m-%d}", "TimeStamp": f"{end:%H:%M:%S}",
                 "R": {"Scale": "1", "Text": "Minor"}, "S": {"Scale": "0", "Text": "none"},
                 "G": {"Scale": "0", "Text": "none"}}
        swxkit.publish_event("noaa.scales", {"days": {"0": today}}, valid_from=end, issued=end)

        # A week of baseline issues, through the product's own collector (so
        # through swxkit.publish_forecast), then scored like the nightly job.
        observations = swxkit.read_series("noaa.kp", "kp", start - dt.timedelta(days=1))
        for product_id in FORECAST_PRODUCTS:
            manifest = get_manifest(product_id)
            collect = importlib.import_module(f"modules.{manifest['_module']}.collectors").COLLECTORS[product_id]
            Forecast.objects.filter(product_id=product_id).delete()
            issued_at = swxkit.floor_issue_time(product_id, start + dt.timedelta(hours=3))
            rows = 0
            while issued_at <= end:
                rows += collect(issued_at=issued_at, observations=observations)
                issued_at += dt.timedelta(hours=1)
            scores = verify_product(Product.objects.get(id=product_id), timezone.now())
            self.stdout.write(f"{product_id}: {rows} forecast rows, {scores} score rows")
        self.stdout.write(self.style.SUCCESS(f"seeded {options['days']:g} day(s) ending {end:%Y-%m-%d %H:%M} UT"))
