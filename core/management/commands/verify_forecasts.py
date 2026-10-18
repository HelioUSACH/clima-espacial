"""`manage.py verify_forecasts`: score every forecast product against its
observed target (D-075). Run daily by swx-verify.timer.

Per (product, model_version, lead, site) and window (7/30/90 days of valid
times): n, MAE, RMSE, bias, skill against persistence and, with bounds,
coverage next to its nominal level (`core.forecasting.scores`). The matching
rule and the persistence reference are `core.forecasting`'s (D-074), and the
reference is computed from the observations, never from a baseline product.

Observations are the target as first stored: the store is append-only
(D-049), so a provider's later revision of a value is never seen.

The persistence reference is what was knowable when each forecast was
published: an observation stored after the forecast's `inserted_at` doesn't
count (D-079). When no observation passes that test (seeded data, a
backfill), the reference falls back to the `time_tag` rule alone and those
pairs are counted in `n_reference_fallback`, never mixed in silently.

A product whose manifest says `baseline: persistence` *is* that reference,
so its issued values should equal the reconstructed one. More than
MISMATCH_TOLERANCE of mismatches over the last week means the issue timing
broke again (the target arrived late, a collector was down) and is logged as
a warning.
"""

import datetime as dt
import logging
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.utils import timezone

from core import forecasting, swxkit
from core.manifests.loader import ManifestError, get_manifest, parse_duration
from core.models import Forecast, ForecastScore, Product

logger = logging.getLogger(__name__)

WINDOWS_DAYS = (7, 30, 90)
MISMATCH_WINDOW_DAYS = 7
MISMATCH_TOLERANCE = 0.01


def _reference(observations, inserted, times, issued_at, step, known_at):
    """(reference observation or None, fell back to the time_tag rule)."""
    ref = forecasting.persistence(observations, issued_at, step, times=times, inserted=inserted, known_at=known_at)
    if ref is not None:
        return ref, False
    ref = forecasting.persistence(observations, issued_at, step, times=times)
    return ref, ref is not None


def verify_product(product: Product, now: dt.datetime) -> int:
    """Upsert this product's scores; returns the number of score rows."""
    manifest = get_manifest(product.id)
    spec = manifest["forecast"]
    target, field = spec["target"]["product"], spec["target"]["field"]
    uncertainty = spec["uncertainty"]
    nominal = uncertainty.get("level") if uncertainty["type"] == "bounds" else None
    is_baseline = spec.get("baseline") == "persistence"
    longest = dt.timedelta(days=max(WINDOWS_DAYS))
    max_lead = dt.timedelta(seconds=max(parse_duration(h) for h in spec["horizons"]))

    rows = (
        Forecast.objects.filter(product=product, valid_at__gt=now - longest, valid_at__lte=now)
        .values_list("model_version", "lead_seconds", "site", "issued_at", "valid_at", "values", "inserted_at")
    )
    groups = defaultdict(list)
    for model_version, lead, site, issued_at, valid_at, values, inserted_at in rows:
        groups[(model_version, lead, site)].append((issued_at, valid_at, values.get(field) or {}, inserted_at))

    observations_by_site = {}
    written = 0
    mismatches = checked = 0
    for (model_version, lead, site), items in sorted(groups.items()):
        if site not in observations_by_site:
            # Enough history for the oldest issue's persistence reference too.
            stored = swxkit.read_series(target, field, now - longest - max_lead - dt.timedelta(days=1),
                                        site=site, with_inserted=True)
            observations = [(t, v) for t, v, _i in stored]
            observations_by_site[site] = (observations, [i for _t, _v, i in stored],
                                          forecasting.target_step(observations), [t for t, _v in observations])
        observations, inserted, step, times = observations_by_site[site]

        # Each forecast is matched once; the windows below only filter.
        verified, not_found = [], []
        for issued_at, valid_at, entry, published_at in items:
            # A valid time whose verifying interval hasn't closed yet is
            # neither verified nor "missing": it's simply not due.
            if step is None or valid_at > now - dt.timedelta(seconds=step):
                continue
            observed = forecasting.matching_observation(observations, valid_at, step, times=times)
            if observed is None or entry.get("value") is None:
                not_found.append(valid_at)
                continue
            ref, fell_back = _reference(observations, inserted, times, issued_at, step, published_at)
            verified.append((valid_at, fell_back, {
                "forecast": entry["value"], "observed": observed[1], "persistence": ref[1] if ref else None,
                "lower": entry.get("lower"), "upper": entry.get("upper")}))
            if is_baseline and ref is not None and not fell_back and valid_at > now - dt.timedelta(days=MISMATCH_WINDOW_DAYS):
                checked += 1
                mismatches += abs(entry["value"] - ref[1]) > 1e-9

        for window in WINDOWS_DAYS:
            since = now - dt.timedelta(days=window)
            in_window = [(fell_back, pair) for valid_at, fell_back, pair in verified if valid_at > since]
            result = forecasting.scores([pair for _f, pair in in_window], nominal_coverage=nominal)
            ForecastScore.objects.update_or_create(
                product=product, model_version=model_version, lead_seconds=lead, site=site, window_days=window,
                defaults={**result, "n_unverified": sum(1 for v in not_found if v > since),
                          "n_reference_fallback": sum(1 for fell_back, _p in in_window if fell_back),
                          "computed_at": now},
            )
            written += 1
    if checked and mismatches / checked > MISMATCH_TOLERANCE:
        logger.warning(
            "%s: %d of %d issued values in the last %d days differ from the persistence reference "
            "knowable at publication; the issue timing is off (target late, collector down?)",
            product.id, mismatches, checked, MISMATCH_WINDOW_DAYS,
        )
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
            fallback = sum(ForecastScore.objects.filter(product=product, window_days=max(WINDOWS_DAYS))
                           .values_list("n_reference_fallback", flat=True))
            note = f", {fallback} pair(s) with a replay reference (time_tag rule)" if fallback else ""
            self.stdout.write(self.style.SUCCESS(f"{product.id}: {n} score row(s){note}"))
