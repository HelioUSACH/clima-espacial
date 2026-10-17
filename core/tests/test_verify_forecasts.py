"""D-075: `manage.py verify_forecasts` end to end on a stored Kp series and
stored baseline issues."""

import datetime as dt

import pytest
from django.core.management import call_command
from django.utils import timezone

from core import swxkit
from core.management.commands.verify_forecasts import verify_product
from core.models import Forecast, ForecastScore, Product
from modules.baselines import collectors

H = dt.timedelta(hours=1)
PID = collectors.PRODUCT_ID


def _seed(now, days=4):
    start = (now - dt.timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    start -= dt.timedelta(hours=start.hour % 3)
    rows, t, i = [], start, 0
    while t <= now:
        rows.append({"time_tag": t, "kp": float((i * 7) % 6)})  # varied, deterministic
        t += 3 * H
        i += 1
    swxkit.publish_timeseries("noaa.kp", rows)
    obs = swxkit.read_series("noaa.kp", "kp", start)
    issued = swxkit.floor_issue_time(PID, start + 4 * H)
    while issued <= now:
        collectors.collect_kp_persistence(issued_at=issued, observations=obs)
        issued += H


@pytest.mark.django_db
def test_persistence_verified_against_itself_has_zero_skill():
    now = timezone.now()
    _seed(now)
    n = verify_product(Product.objects.get(id=PID), now)
    assert n == 4 * 3  # 4 leads x 3 windows
    for score in ForecastScore.objects.filter(product_id=PID):
        assert score.n >= 20
        assert score.skill == 0.0
        assert score.nominal_coverage == 0.8
        assert 0 <= score.coverage <= 1
        assert score.mae is not None and score.rmse >= score.mae


@pytest.mark.django_db
def test_forecasts_not_yet_due_are_neither_verified_nor_missing():
    now = timezone.now()
    _seed(now, days=3)
    verify_product(Product.objects.get(id=PID), now)
    due = Forecast.objects.filter(product_id=PID, lead_seconds=3 * 3600, valid_at__lte=now - 3 * H).count()
    score = ForecastScore.objects.get(product_id=PID, lead_seconds=3 * 3600, window_days=7)
    assert score.n + score.n_unverified == due


@pytest.mark.django_db
def test_a_missing_observation_is_counted_not_guessed():
    now = timezone.now()
    _seed(now)
    # Delete one complete Kp interval two days ago: every forecast valid in it is unverified.
    hole = (now - dt.timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    hole -= dt.timedelta(hours=hole.hour % 3)
    from core.models import TimeSeriesPoint
    TimeSeriesPoint.objects.filter(product_id="noaa.kp", time_tag=hole).delete()
    verify_product(Product.objects.get(id=PID), now)
    assert ForecastScore.objects.get(product_id=PID, lead_seconds=3 * 3600, window_days=7).n_unverified == 3


@pytest.mark.django_db
def test_rerun_upserts_and_drops_scores_with_nothing_left():
    now = timezone.now()
    _seed(now, days=3)
    product = Product.objects.get(id=PID)
    verify_product(product, now)
    ForecastScore.objects.create(product=product, model_version="0.0.1", lead_seconds=10800, window_days=7,
                                 computed_at=now - dt.timedelta(days=1))
    verify_product(product, now + dt.timedelta(minutes=1))
    assert ForecastScore.objects.filter(product=product).count() == 12
    assert not ForecastScore.objects.filter(model_version="0.0.1").exists()


@pytest.mark.django_db
def test_command_runs_with_no_forecast_products():
    call_command("verify_forecasts")
