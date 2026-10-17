"""`manage.py verify_forecasts`: score every forecast product against its
observed target (D-075). Run daily by swx-verify.timer.

Per (product, model_version, lead, site) and window (7/30/90 days of valid
times): n, MAE, RMSE, bias, skill against persistence and, with bounds,
coverage next to its nominal level (`core.forecasting.scores`). The matching
rule and the persistence reference are `core.forecasting`'s (D-074), and the
reference is computed from the observations, never from a baseline product.

Observations are the target as first stored: the store is append-only
(D-049), so a provider's later revision of a value is never seen.
"""

import datetime as dt
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.utils import timezone

from core import forecasting, swxkit
from core.manifests.loader import ManifestError, get_manifest, parse_duration
from core.models import Forecast, ForecastScore, Product

WINDOWS_DAYS = (7, 30, 90)


def verify_product(product: Product, now: dt.datetime) -> int:
    """Upsert this product's scores; returns the number of score rows."""
    manifest = get_manifest(product.id)
    spec = manifest["forecast"]
    target, field = spec["target"]["product"], spec["target"]["field"]
    uncertainty = spec["uncertainty"]
    nominal = uncertainty.get("level") if uncertainty["type"] == "bounds" else None
    longest = dt.timedelta(days=max(WINDOWS_DAYS))
    max_lead = dt.timedelta(seconds=max(parse_duration(h) for h in spec["horizons"]))

    rows = (
        Forecast.objects.filter(product=product, valid_at__gt=now - longest, valid_at__lte=now)
        .values_list("model_version", "lead_seconds", "site", "issued_at", "valid_at", "values")
    )
    groups = defaultdict(list)
    for model_version, lead, site, issued_at, valid_at, values in rows:
        groups[(model_version, lead, site)].append((issued_at, valid_at, values.get(field) or {}))

    observations_by_site = {}
    written = 0
    for (model_version, lead, site), items in sorted(groups.items()):
        if site not in observations_by_site:
            # Enough history for the oldest issue's persistence reference too.
            observations = swxkit.read_series(target, field, now - longest - max_lead - dt.timedelta(days=1), site=site)
            observations_by_site[site] = (observations, forecasting.target_step(observations),
                                          [t for t, _v in observations])
        observations, step, times = observations_by_site[site]
        for window in WINDOWS_DAYS:
            since = now - dt.timedelta(days=window)
            pairs, unverified = [], 0
            for issued_at, valid_at, entry in items:
                # A valid time whose verifying interval hasn't closed yet is
                # neither verified nor "missing": it's simply not due.
                if valid_at <= since or step is None or valid_at > now - dt.timedelta(seconds=step):
                    continue
                observed = forecasting.matching_observation(observations, valid_at, step, times=times)
                if observed is None or entry.get("value") is None:
                    unverified += 1
                    continue
                ref = forecasting.persistence(observations, issued_at, step, times=times)
                pairs.append({"forecast": entry["value"], "observed": observed[1],
                              "persistence": ref[1] if ref else None,
                              "lower": entry.get("lower"), "upper": entry.get("upper")})
            result = forecasting.scores(pairs, nominal_coverage=nominal)
            ForecastScore.objects.update_or_create(
                product=product, model_version=model_version, lead_seconds=lead, site=site, window_days=window,
                defaults={**result, "n_unverified": unverified, "computed_at": now},
            )
            written += 1
    # A (version, lead, site) with nothing left in the longest window is
    # history, not a current score.
    ForecastScore.objects.filter(product=product, computed_at__lt=now).delete()
    return written


class Command(BaseCommand):
    help = "Score every kind=forecast product against its observed target (7/30/90-day windows)."

    def handle(self, *args, **options):
        now = timezone.now()
        for product in Product.objects.filter(kind="forecast").order_by("id"):
            try:
                n = verify_product(product, now)
            except ManifestError as exc:
                self.stderr.write(self.style.ERROR(f"{product.id}: {exc}"))
                continue
            self.stdout.write(self.style.SUCCESS(f"{product.id}: {n} score row(s)"))
