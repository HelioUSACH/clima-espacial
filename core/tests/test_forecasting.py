"""D-074/D-075: the matching rule, the persistence reference and the
verification metrics, against hand-built series and hand-computed numbers."""

import datetime as dt
import math

import pytest

from core import forecasting

T0 = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
H = dt.timedelta(hours=1)
STEP = 3 * 3600


def kp_series(values):
    """A Kp-like series: one value per 3-hour interval, time_tag at the start."""
    return [(T0 + i * 3 * H, float(v)) for i, v in enumerate(values)]


def test_target_step_is_the_median_spacing():
    obs = kp_series([1, 2, 3, 4])
    obs.insert(2, (T0 + 4 * H, 9.0))  # one stray sample doesn't change the median
    assert forecasting.target_step(obs) == STEP
    assert forecasting.target_step(obs[:1]) is None


@pytest.mark.parametrize("valid_hour, expected_start_hour", [
    (17 - 15, 0),   # T0+02:00 is inside the 00-03 interval
    (3, 3),         # an interval's own start belongs to it
    (5, 3),
    (6, 6),
    (8.99, 6),
])
def test_matching_rule_picks_the_interval_containing_valid_at(valid_hour, expected_start_hour):
    obs = kp_series([1, 2, 3, 4])
    match = forecasting.matching_observation(obs, T0 + dt.timedelta(hours=valid_hour), STEP)
    assert match[0] == T0 + expected_start_hour * H


def test_matching_rule_kp_at_17_is_the_15_to_18_interval():
    # The handoff's own example, on wall-clock hours.
    day = dt.datetime(2026, 10, 17, tzinfo=dt.UTC)
    obs = [(day + i * 3 * H, float(i)) for i in range(8)]  # 00, 03, ..., 21
    match = forecasting.matching_observation(obs, day + 17 * H, STEP)
    assert match == (day + 15 * H, 5.0)


def test_matching_rule_skips_a_gap_instead_of_guessing():
    obs = [(T0, 1.0), (T0 + 9 * H, 4.0)]  # 03:00 and 06:00 intervals missing
    assert forecasting.matching_observation(obs, T0 + 4 * H, STEP) is None
    assert forecasting.matching_observation(obs, T0 - H, STEP) is None


def test_matching_rule_on_minute_cadence_is_the_sample_at_or_before():
    obs = [(T0 + i * dt.timedelta(minutes=1), float(i)) for i in range(10)]
    assert forecasting.matching_observation(obs, T0 + dt.timedelta(minutes=4, seconds=30), 60)[1] == 4.0


def test_persistence_is_the_last_complete_interval():
    obs = kp_series([1, 2, 3, 4])  # 00, 03, 06, 09
    # At 08:00 the 06-09 interval is still open: the 03-06 one is the last complete.
    assert forecasting.persistence(obs, T0 + 8 * H, STEP) == (T0 + 3 * H, 2.0)
    # At 09:00 exactly, 06-09 has closed.
    assert forecasting.persistence(obs, T0 + 9 * H, STEP) == (T0 + 6 * H, 3.0)
    assert forecasting.persistence(obs, T0 + 2 * H, STEP) is None


def test_percentile_matches_linear_interpolation():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert forecasting.percentile(values, 0.1) == pytest.approx(1.9)
    assert forecasting.percentile(values, 0.9) == pytest.approx(9.1)
    assert forecasting.percentile([5], 0.5) == 5


def test_scores_hand_computed():
    pairs = [
        {"forecast": 2.0, "observed": 1.0, "persistence": 1.0, "lower": 1.0, "upper": 3.0},   # e=+1, inside
        {"forecast": 2.0, "observed": 4.0, "persistence": 1.0, "lower": 1.0, "upper": 3.0},   # e=-2, outside
        {"forecast": 3.0, "observed": 3.0, "persistence": 4.0, "lower": 2.0, "upper": 4.0},   # e=0, inside
    ]
    out = forecasting.scores(pairs, nominal_coverage=0.8)
    assert out["n"] == 3
    assert out["mae"] == pytest.approx(1.0)
    assert out["rmse"] == pytest.approx(math.sqrt(5 / 3))
    assert out["bias"] == pytest.approx(-1 / 3)
    assert out["coverage"] == pytest.approx(2 / 3)
    assert out["nominal_coverage"] == 0.8
    assert out["skill"] is None  # n < 20


def test_skill_against_persistence_hand_computed():
    # 20 identical blocks: forecast errors 1, persistence errors 2 -> 1 - 1/4.
    pairs = [{"forecast": 3.0, "observed": 2.0, "persistence": 4.0}] * 20
    assert forecasting.scores(pairs)["skill"] == pytest.approx(0.75)


def test_persistence_scored_against_itself_has_zero_skill():
    pairs = [{"forecast": float(i % 4), "observed": float((i + 1) % 4), "persistence": float(i % 4)} for i in range(25)]
    assert forecasting.scores(pairs)["skill"] == 0.0


def test_skill_is_null_without_reference_spread():
    pairs = [{"forecast": 3.0, "observed": 2.0, "persistence": 2.0}] * 30  # MSE_persistence = 0
    assert forecasting.scores(pairs)["skill"] is None


def test_skill_needs_twenty_pairs_with_a_reference():
    pairs = [{"forecast": 3.0, "observed": 2.0, "persistence": 4.0}] * 19
    pairs += [{"forecast": 3.0, "observed": 2.0, "persistence": None}] * 10
    out = forecasting.scores(pairs)
    assert out["n"] == 29 and out["skill"] is None


def test_scores_of_nothing_are_null():
    out = forecasting.scores([])
    assert out["n"] == 0 and out["mae"] is None and out["coverage"] is None


def test_persistence_known_at_skips_what_was_not_yet_stored():
    obs = kp_series([1, 2, 3, 4])  # 00, 03, 06, 09
    # Each interval stored 10 min after it closes.
    inserted = [t + dt.timedelta(hours=3, minutes=10) for t, _v in obs]
    issued = T0 + 9 * H
    # At 09:05 the 06-09 interval had closed but wasn't stored yet.
    assert forecasting.persistence(obs, issued, STEP, inserted=inserted, known_at=issued + dt.timedelta(minutes=5)) == (T0 + 3 * H, 2.0)
    # At 09:35 it was.
    assert forecasting.persistence(obs, issued, STEP, inserted=inserted, known_at=issued + dt.timedelta(minutes=35)) == (T0 + 6 * H, 3.0)
    # Nothing stored by then: None (the caller decides on a fallback).
    assert forecasting.persistence(obs, issued, STEP, inserted=inserted, known_at=T0) is None
