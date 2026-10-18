"""Forecast verification rules (D-074, D-075): one definition each, shared by
every producer and by `manage.py verify_forecasts`.

Observations are plain sorted lists of `(time_tag, value)` as returned by
`swxkit.read_series` — no database access here, so every rule is a pure
function, testable against a hand-built series.

**Time convention.** A target's `time_tag` is the *start* of the interval it
describes (NOAA's Kp: the 15:00 row is the 15:00–18:00 interval). For a
minute-cadence series the "interval" is just the sample's own minute.
"""

from __future__ import annotations

import bisect
import datetime as dt
import math
from collections.abc import Sequence

Observation = tuple[dt.datetime, float]

# Below this many pairs a skill score is noise, not a measurement (D-075).
MIN_PAIRS_FOR_SKILL = 20


def target_step(observations: Sequence[Observation]) -> int | None:
    """The target's native step in seconds, read from its data: the median
    spacing of consecutive samples (D-056 does the same for chart gaps).
    Kp gives 10800 whatever its polling `cadence`. None with < 2 samples."""
    if len(observations) < 2:
        return None
    deltas = sorted(
        int((b[0] - a[0]).total_seconds()) for a, b in zip(observations, observations[1:], strict=False)
    )
    return deltas[len(deltas) // 2]


def _times(observations: Sequence[Observation]) -> list[dt.datetime]:
    return [t for t, _v in observations]


def matching_observation(
    observations: Sequence[Observation], valid_at: dt.datetime, step: int, *, times: list | None = None
) -> Observation | None:
    """**Matching rule (D-074).** The observation that verifies a forecast
    valid at `valid_at` is the sample whose interval contains it: `time_tag`
    in `(valid_at - step, valid_at]`. A Kp forecast valid at 17:00 is
    verified against the 15:00 (15:00–18:00) interval; for a minute-cadence
    target this is the sample at or just before `valid_at`. None if there
    is none (the pair is skipped and counted, never guessed)."""
    times = times if times is not None else _times(observations)
    i = bisect.bisect_right(times, valid_at) - 1
    if i < 0:
        return None
    t, v = observations[i]
    if t <= valid_at - dt.timedelta(seconds=step):
        return None
    return t, v


def persistence(
    observations: Sequence[Observation], issued_at: dt.datetime, step: int, *, times: list | None = None,
    inserted: Sequence[dt.datetime] | None = None, known_at: dt.datetime | None = None,
) -> Observation | None:
    """**Persistence reference (D-074, D-079).** The last *complete*
    observation at `issued_at`: the newest sample with `time_tag + step <=
    issued_at` (an interval still in progress is not yet known). Every model's
    skill is measured against this, computed from the observations
    themselves, so no score depends on the `usach.kp.persistence` product
    being up.

    With `known_at` (and `inserted`, each observation's stored-at time,
    aligned with `observations`), the sample must also have been in the store
    by `known_at`: what was knowable when the forecast was published. A Kp
    interval reaches the store minutes after it closes, so by `time_tag` alone
    the reference would use an interval no forecaster had yet. None if no
    sample qualifies; the caller decides whether to fall back."""
    times = times if times is not None else _times(observations)
    i = bisect.bisect_right(times, issued_at - dt.timedelta(seconds=step)) - 1
    if known_at is not None and inserted is not None:
        while i >= 0 and inserted[i] > known_at:
            i -= 1
    return observations[i] if i >= 0 else None


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (the usual "type 7" definition, as
    numpy's default), q in [0, 1]. No numpy on the VM for one function."""
    if not values:
        raise ValueError("percentile of an empty sequence")
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def scores(pairs: Sequence[dict], *, nominal_coverage: float | None = None) -> dict:
    """**Verification metrics (D-075)** over pairs
    `{"forecast", "observed", "persistence", "lower", "upper"}` (the last
    three may be None). Errors are forecast − observed.

    - n, MAE, RMSE, bias over every pair;
    - skill = 1 − MSE / MSE_persistence, both MSEs over the pairs that
      have a persistence value; null when fewer than MIN_PAIRS_FOR_SKILL of
      those, or MSE_persistence = 0 (no reference spread to beat);
    - coverage = fraction with lower ≤ observed ≤ upper, over pairs that
      carry both bounds; null when none does.
    """
    n = len(pairs)
    out = {"n": n, "mae": None, "rmse": None, "bias": None, "skill": None,
           "coverage": None, "nominal_coverage": nominal_coverage}
    if not n:
        return out
    errors = [p["forecast"] - p["observed"] for p in pairs]
    out["mae"] = sum(abs(e) for e in errors) / n
    out["rmse"] = math.sqrt(sum(e * e for e in errors) / n)
    out["bias"] = sum(errors) / n

    with_ref = [p for p in pairs if p.get("persistence") is not None]
    if len(with_ref) >= MIN_PAIRS_FOR_SKILL:
        mse = sum((p["forecast"] - p["observed"]) ** 2 for p in with_ref) / len(with_ref)
        mse_ref = sum((p["persistence"] - p["observed"]) ** 2 for p in with_ref) / len(with_ref)
        if mse_ref > 0:
            out["skill"] = 1 - mse / mse_ref

    bounded = [p for p in pairs if p.get("lower") is not None and p.get("upper") is not None]
    if bounded:
        out["coverage"] = sum(1 for p in bounded if p["lower"] <= p["observed"] <= p["upper"]) / len(bounded)
    return out
