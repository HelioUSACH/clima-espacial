"""The Kp persistence baseline, as a pure function (D-073/D-074): given the
observed Kp series and an issue time, the rows of that issue. No database
access, so it is tested directly and `seed_dev_data` can replay it.

- value = the persistence reference `core.forecasting.persistence` (the last
  *complete* Kp interval at `issued_at`), so this product's skill against
  persistence is 0 by construction;
- lower/upper = value + the 10th/90th percentile of persistence's own errors
  Kp(t+L) − Kp(t) at that lead, over hourly issue times in the last 30 days
  (nominal 0.8), using only intervals already complete at `issued_at` (no
  look-ahead), widened if needed to contain the value, clipped to [0, 9].
"""

from __future__ import annotations

import datetime as dt

from core import forecasting

MODEL_VERSION = "0.1.0"
PRODUCER = f"baselines.kp_persistence@{MODEL_VERSION}"
ERROR_WINDOW = dt.timedelta(days=30)
# With fewer persistence errors than this at a lead the percentiles mean
# nothing: the band is then the whole Kp range, an honest "unknown".
MIN_ERRORS = 20
# Newest Kp older than this at issue time: publish nothing (target stale).
MAX_TARGET_AGE = dt.timedelta(hours=9)
KP_RANGE = (0.0, 9.0)
LOWER_Q, UPPER_Q = 0.1, 0.9


class TargetStale(Exception):
    """The target series is too old (or too short) to issue honestly."""


def _clip(value: float) -> float:
    return min(KP_RANGE[1], max(KP_RANGE[0], value))


def persistence_errors(observations, issued_at, lead_seconds, step):
    """Persistence's errors at one lead, as they were knowable at
    `issued_at`: for each hourly issue time τ in the last 30 days with
    τ + lead <= issued_at, observed(τ + lead) − persistence(τ)."""
    known = [o for o in observations if o[0] + dt.timedelta(seconds=step) <= issued_at]
    times = [t for t, _v in known]
    lead = dt.timedelta(seconds=lead_seconds)
    errors = []
    tau = issued_at - ERROR_WINDOW
    while tau + lead <= issued_at:
        ref = forecasting.persistence(known, tau, step, times=times)
        obs = forecasting.matching_observation(known, tau + lead, step, times=times)
        if ref is not None and obs is not None:
            errors.append(obs[1] - ref[1])
        tau += dt.timedelta(hours=1)
    return errors


def issue(observations, issued_at: dt.datetime, horizons_seconds, field: str = "kp"):
    """-> (rows, newest_time_tag_used). `observations`: sorted
    `[(time_tag, kp)]` from `swxkit.read_series`. Raises TargetStale."""
    observations = [o for o in observations if o[0] <= issued_at]
    if not observations:
        raise TargetStale("no Kp observations before the issue time")
    newest = observations[-1][0]
    if issued_at - newest > MAX_TARGET_AGE:
        raise TargetStale(f"newest Kp is {newest:%Y-%m-%dT%H:%MZ}, older than {MAX_TARGET_AGE} at issue time")
    step = forecasting.target_step(observations)
    ref = forecasting.persistence(observations, issued_at, step) if step else None
    if ref is None:
        raise TargetStale("no complete Kp interval before the issue time")
    value = ref[1]
    rows = []
    for lead in horizons_seconds:
        errors = persistence_errors(observations, issued_at, lead, step)
        if len(errors) >= MIN_ERRORS:
            lower = min(value, _clip(value + forecasting.percentile(errors, LOWER_Q)))
            upper = max(value, _clip(value + forecasting.percentile(errors, UPPER_Q)))
        else:
            lower, upper = KP_RANGE
        # Rounded bounds must still contain the unrounded value: Kp 2.333...
        # with a zero error percentile would otherwise get upper 2.333 < value,
        # which publish_forecast rejects (the whole issue would be lost).
        rows.append({
            "valid_at": issued_at + dt.timedelta(seconds=lead),
            field: {"value": value, "lower": min(value, round(lower, 3)), "upper": max(value, round(upper, 3))},
        })
    return rows, ref[0]
