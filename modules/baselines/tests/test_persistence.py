"""The Kp persistence baseline (v0.4.0 §4): its value is the shared
persistence reference, its bounds come from persistence's own past errors
without look-ahead, and a stale target publishes nothing."""

import datetime as dt

import pytest

from core import forecasting
from core.models import Forecast, Heartbeat
from modules.baselines import collectors, persistence

H = dt.timedelta(hours=1)
HORIZONS = [3 * 3600, 6 * 3600, 12 * 3600, 24 * 3600]
ISSUED = dt.datetime(2026, 10, 17, 19, tzinfo=dt.UTC)


def kp_history(end, days=31, pattern=(1, 2, 3, 4, 3, 2)):
    """3-hourly Kp up to `end` (time_tag at interval start), repeating pattern."""
    start = end - dt.timedelta(days=days)
    start = start.replace(hour=start.hour - start.hour % 3, minute=0, second=0, microsecond=0)
    out, t, i = [], start, 0
    while t <= end:
        out.append((t, float(pattern[i % len(pattern)])))
        t += 3 * H
        i += 1
    return out


def test_value_is_the_shared_persistence_reference():
    obs = kp_history(ISSUED)
    rows, newest = persistence.issue(obs, ISSUED, HORIZONS)
    ref = forecasting.persistence(obs, ISSUED, 3 * 3600)
    assert ref[0] == dt.datetime(2026, 10, 17, 15, tzinfo=dt.UTC)  # 15-18 is the last complete at 19:00
    assert newest == ref[0]
    assert all(r["kp"]["value"] == ref[1] for r in rows)
    assert [r["valid_at"] - ISSUED for r in rows] == [dt.timedelta(seconds=s) for s in HORIZONS]


def test_bounds_contain_the_value_widen_with_spread_and_stay_in_range():
    rows, _ = persistence.issue(kp_history(ISSUED), ISSUED, HORIZONS)
    for r in rows:
        kp = r["kp"]
        assert 0 <= kp["lower"] <= kp["value"] <= kp["upper"] <= 9


def test_rounded_bounds_still_contain_a_thirds_value():
    # Kp in thirds (2.333...) with no spread in its errors: the band is the
    # value itself, and rounding must not push it below/above the value.
    rows, _ = persistence.issue(kp_history(ISSUED, pattern=(7 / 3,)), ISSUED, HORIZONS)
    for r in rows:
        kp = r["kp"]
        assert kp["lower"] <= kp["value"] <= kp["upper"], kp


def test_bounds_are_the_error_percentiles():
    obs = kp_history(ISSUED)
    errors = persistence.persistence_errors(obs, ISSUED, 3 * 3600, 3 * 3600)
    value = forecasting.persistence(obs, ISSUED, 3 * 3600)[1]
    rows, _ = persistence.issue(obs, ISSUED, [3 * 3600])
    assert rows[0]["kp"]["lower"] == pytest.approx(min(value, max(0, value + forecasting.percentile(errors, 0.1))), abs=1e-3)
    assert rows[0]["kp"]["upper"] == pytest.approx(max(value, min(9, value + forecasting.percentile(errors, 0.9))), abs=1e-3)


def test_errors_never_look_past_the_issue_time():
    obs = kp_history(ISSUED)
    future = [(ISSUED + i * 3 * H, 9.0) for i in range(1, 10)]  # a storm after the issue
    assert persistence.issue(obs, ISSUED, HORIZONS) == persistence.issue(obs + future, ISSUED, HORIZONS)


def test_too_few_errors_give_the_full_range():
    obs = kp_history(ISSUED, days=1.5)  # ~9 hourly 24 h errors, under MIN_ERRORS
    rows, _ = persistence.issue(obs, ISSUED, [24 * 3600])
    assert (rows[0]["kp"]["lower"], rows[0]["kp"]["upper"]) == (0.0, 9.0)


def test_stale_target_raises():
    obs = kp_history(ISSUED - 10 * H)
    with pytest.raises(persistence.TargetStale):
        persistence.issue(obs, ISSUED, HORIZONS)
    with pytest.raises(persistence.TargetStale):
        persistence.issue([], ISSUED, HORIZONS)


@pytest.mark.django_db
def test_collector_publishes_once_per_hour_and_is_idempotent():
    obs = kp_history(ISSUED)
    assert collectors.collect_kp_persistence(issued_at=ISSUED + 25 * dt.timedelta(minutes=1), observations=obs) == 4
    assert collectors.collect_kp_persistence(issued_at=ISSUED, observations=obs) == 0
    row = Forecast.objects.filter(product_id=collectors.PRODUCT_ID).first()
    assert row.issued_at == ISSUED
    assert row.provenance == {"inputs": [{"product": "noaa.kp", "latest": "2026-10-17T15:00:00Z"}],
                              "producer": persistence.PRODUCER}


@pytest.mark.django_db
def test_collector_marks_error_on_stale_target_and_publishes_nothing():
    assert collectors.collect_kp_persistence(issued_at=ISSUED, observations=kp_history(ISSUED - 12 * H)) == 0
    hb = Heartbeat.objects.get(product_id=collectors.PRODUCT_ID)
    assert hb.status == "error" and hb.detail.startswith("target stale")
    assert not Forecast.objects.filter(product_id=collectors.PRODUCT_ID).exists()
