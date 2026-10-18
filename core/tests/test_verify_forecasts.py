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


# D-079: the persistence reference is what was in the store when the forecast
# was published. Live timing on helio: a Kp interval is stored ~10 min after
# it closes, and the baseline used to run at :05.
LAG = dt.timedelta(minutes=10)
RUN_AT = dt.timedelta(minutes=5)


def _seed_live(now, *, hindsight=False, days=4):
    """Kp stored LAG after each interval closes, and baseline issues published
    RUN_AT past the hour from what was stored by then (or, hindsight=True,
    from every observation: the wrong values a too-early run can't have)."""
    from django.db.models import F

    from core.models import TimeSeriesPoint
    start = (now - dt.timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    start -= dt.timedelta(hours=start.hour % 3)
    rows, t, i = [], start, 0
    while t + 3 * H + LAG <= now:
        rows.append({"time_tag": t, "kp": float((i * 7) % 6)})
        t += 3 * H
        i += 1
    swxkit.publish_timeseries("noaa.kp", rows)
    TimeSeriesPoint.objects.filter(product_id="noaa.kp").update(inserted_at=F("time_tag") + 3 * H + LAG)
    stored = swxkit.read_series("noaa.kp", "kp", start, with_inserted=True)
    issued = swxkit.floor_issue_time(PID, start + 4 * H)
    while issued + RUN_AT <= now:
        published = issued + RUN_AT
        known = [(t, v) for t, v, ins in stored if hindsight or ins <= published]
        collectors.collect_kp_persistence(issued_at=issued, observations=known)
        Forecast.objects.filter(product_id=PID, issued_at=issued).update(inserted_at=published)
        issued += H


@pytest.mark.django_db
def test_reference_is_what_was_stored_when_the_forecast_was_published(caplog):
    now = timezone.now()
    _seed_live(now)
    with caplog.at_level("WARNING", logger="core.management.commands.verify_forecasts"):
        verify_product(Product.objects.get(id=PID), now)
    for score in ForecastScore.objects.filter(product_id=PID):
        assert score.skill == 0.0
        assert score.n_reference_fallback == 0
    assert "differ from the persistence reference" not in caplog.text


@pytest.mark.django_db
def test_the_time_tag_rule_alone_would_not_give_the_baseline_zero_skill():
    # The same live data scored the v0.4.0 way: the reference picks the interval
    # that had closed but not yet arrived, at the 00/03/.../21 UT issues.
    from core import forecasting
    now = timezone.now()
    _seed_live(now)
    obs = swxkit.read_series("noaa.kp", "kp", now - dt.timedelta(days=5))
    step = forecasting.target_step(obs)
    pairs = []
    for f in Forecast.objects.filter(product_id=PID, lead_seconds=3 * 3600, valid_at__lte=now - 3 * H):
        observed = forecasting.matching_observation(obs, f.valid_at, step)
        ref = forecasting.persistence(obs, f.issued_at, step)
        if observed and ref:
            pairs.append({"forecast": f.values["kp"]["value"], "observed": observed[1], "persistence": ref[1]})
    assert forecasting.scores(pairs)["skill"] != 0.0


@pytest.mark.django_db
def test_replayed_data_falls_back_to_the_time_tag_rule_and_counts_it():
    # Observations stored after every forecast (a backfill or a replay): no
    # observation passes the inserted_at test, so the time_tag rule is used
    # and every pair is counted as a fallback.
    from core.models import TimeSeriesPoint
    now = timezone.now()
    _seed(now)
    Forecast.objects.filter(product_id=PID).update(inserted_at=now - dt.timedelta(days=30))
    TimeSeriesPoint.objects.filter(product_id="noaa.kp").update(inserted_at=now)
    verify_product(Product.objects.get(id=PID), now)
    for score in ForecastScore.objects.filter(product_id=PID):
        assert score.n >= 20
        assert score.n_reference_fallback == score.n
        assert score.skill == 0.0


@pytest.mark.django_db
def test_seeded_dev_data_needs_no_fallback():
    now = timezone.now()
    _seed(now)  # observations first, then the replayed issues
    verify_product(Product.objects.get(id=PID), now)
    assert not ForecastScore.objects.filter(product_id=PID, n_reference_fallback__gt=0).exists()


@pytest.mark.django_db
def test_guard_warns_when_the_baseline_stops_matching_its_reference(caplog):
    now = timezone.now()
    _seed_live(now, hindsight=True)
    with caplog.at_level("WARNING", logger="core.management.commands.verify_forecasts"):
        verify_product(Product.objects.get(id=PID), now)
    assert "issued values in the last 7 days differ from the persistence reference" in caplog.text


@pytest.mark.django_db
def test_command_reports_fallback_pairs():
    from io import StringIO

    from core.models import TimeSeriesPoint
    now = timezone.now()
    _seed(now, days=2)
    Forecast.objects.filter(product_id=PID).update(inserted_at=now - dt.timedelta(days=30))
    TimeSeriesPoint.objects.filter(product_id="noaa.kp").update(inserted_at=now)
    out = StringIO()
    call_command("verify_forecasts", stdout=out)
    assert "with a replay reference (time_tag rule)" in out.getvalue()
