"""D-072/D-073: `swxkit.publish_forecast` -- storage, idempotency, every
rejection, heartbeats -- and the `read_series` read side."""

import copy
import datetime as dt
import logging

import pytest

from core import swxkit
from core.manifests.loader import get_manifest
from core.models import Forecast, Heartbeat

PID = "usach.kp.persistence"
ISSUED = dt.datetime(2026, 10, 17, 18, 0, tzinfo=dt.UTC)
PROV = {"inputs": [{"product": "noaa.kp", "latest": "2026-10-17T15:00:00Z"}], "producer": "test@0"}


def _row(hours, value=3.0, lower=2.0, upper=4.0, **extra):
    row = {"valid_at": ISSUED + dt.timedelta(hours=hours), "kp": {"value": value, "lower": lower, "upper": upper}}
    row.update(extra)
    return row


def _issue(rows=None, **kwargs):
    args = {"issued_at": ISSUED, "model_version": "0.1.0", "provenance": PROV,
            "rows": rows if rows is not None else [_row(3), _row(6), _row(12), _row(24)]}
    args.update(kwargs)
    return swxkit.publish_forecast(PID, **args)


@pytest.fixture
def variant(monkeypatch):
    """Serve a modified copy of the baseline's manifest under its own id."""
    real = get_manifest

    def apply(**forecast_changes):
        manifest = copy.deepcopy(real(PID))
        manifest["forecast"].update(forecast_changes)
        monkeypatch.setattr(swxkit, "get_manifest", lambda pid: copy.deepcopy(manifest) if pid == PID else real(pid))
    return apply


@pytest.mark.django_db
def test_publish_stores_one_row_per_valid_time():
    assert _issue() == 4
    rows = list(Forecast.objects.filter(product_id=PID).order_by("valid_at"))
    assert [r.lead_seconds for r in rows] == [10800, 21600, 43200, 86400]
    assert rows[0].values == {"kp": {"value": 3.0, "lower": 2.0, "upper": 4.0}}
    assert rows[0].site == "" and rows[0].model_version == "0.1.0" and rows[0].provenance == PROV
    assert Heartbeat.objects.get(product_id=PID).status == "ok"


@pytest.mark.django_db
def test_republishing_the_same_issue_inserts_nothing():
    assert _issue() == 4
    assert _issue() == 0
    assert Forecast.objects.filter(product_id=PID).count() == 4
    assert Heartbeat.objects.get(product_id=PID).status == "ok"


@pytest.mark.django_db
def test_a_changed_issue_is_ignored_and_logged(caplog):
    _issue()
    with caplog.at_level(logging.WARNING, logger="core.swxkit"):
        assert _issue(rows=[_row(3, value=5.0, upper=6.0), _row(6), _row(12), _row(24)]) == 0
    assert "already stored and the new rows differ" in caplog.text
    assert Forecast.objects.get(product_id=PID, lead_seconds=10800).values["kp"]["value"] == 3.0


@pytest.mark.django_db
def test_a_new_model_version_is_a_separate_issue():
    _issue()
    assert _issue(model_version="0.1.1") == 4
    assert Forecast.objects.filter(product_id=PID).count() == 8


@pytest.mark.django_db
def test_lead_within_tolerance_is_accepted():
    assert _issue(rows=[{"valid_at": ISSUED + dt.timedelta(hours=3, seconds=59), "kp": {"value": 1, "lower": 0, "upper": 2}}]) == 1


@pytest.mark.django_db
def test_zero_rows_is_no_data():
    assert _issue(rows=[]) == 0
    assert Heartbeat.objects.get(product_id=PID).status == "no_data"


@pytest.mark.django_db
@pytest.mark.parametrize("rows, kwargs, fragment", [
    ([_row(4)], {}, "is not a declared horizon"),
    ([_row(3, dst={"value": 1})], {}, "this model forecasts only 'kp'"),
    ([{"valid_at": ISSUED + dt.timedelta(hours=3), "dst": {"value": 1}}], {}, "this model forecasts only 'kp'"),
    ([_row(3, lower=3.5)], {}, "lower <= value <= upper"),
    ([_row(3, upper=2.5)], {}, "lower <= value <= upper"),
    ([{"valid_at": ISSUED + dt.timedelta(hours=3), "kp": {"value": 3}}], {}, "needs numeric 'lower' and 'upper'"),
    ([_row(3, site="SCL")], {}, "sites: false"),
    ([{"valid_at": dt.datetime(2026, 10, 17, 21), "kp": {"value": 3, "lower": 2, "upper": 4}}], {}, "is naive"),
    ([_row(3)], {"issued_at": dt.datetime(2026, 10, 17, 18)}, "is naive"),
    ([_row(3)], {"issued_at": ISSUED + dt.timedelta(minutes=7)}, "not on a 1h boundary"),
    ([_row(3), _row(3)], {}, "repeats valid_at"),
    ([_row(3, value=True)], {}, "needs 'kp'"),
    ([_row(3)], {"model_version": ""}, "model_version"),
])
def test_rejections_write_nothing(rows, kwargs, fragment):
    with pytest.raises(swxkit.PublishError) as exc:
        _issue(rows=rows, **kwargs)
    assert fragment in str(exc.value)
    assert not Forecast.objects.filter(product_id=PID).exists()


@pytest.mark.django_db
def test_quantiles_are_rejected_as_not_implemented(variant):
    variant(uncertainty={"type": "quantiles"})
    with pytest.raises(swxkit.PublishError, match="not implemented in 0.4"):
        _issue()


@pytest.mark.django_db
def test_sites_true_requires_a_site(variant):
    variant(sites=True)
    with pytest.raises(swxkit.PublishError, match="needs a site code"):
        _issue(rows=[_row(3)])
    assert _issue(rows=[_row(3, site="SCL"), _row(3, site="PAC")]) == 2
    assert set(Forecast.objects.values_list("site", flat=True)) == {"SCL", "PAC"}


@pytest.mark.django_db
def test_uncertainty_none_stores_null_bounds_and_refuses_bounds(variant):
    variant(uncertainty={"type": "none"})
    with pytest.raises(swxkit.PublishError, match="uncertainty: none"):
        _issue(rows=[_row(3)])
    assert _issue(rows=[{"valid_at": ISSUED + dt.timedelta(hours=3), "kp": {"value": 3}}]) == 1
    assert Forecast.objects.get().values == {"kp": {"value": 3.0, "lower": None, "upper": None}}


@pytest.mark.django_db
def test_iso_strings_with_offsets_are_accepted():
    rows = [{"valid_at": "2026-10-17T21:00:00Z", "kp": {"value": 3, "lower": 2, "upper": 4}}]
    assert _issue(rows=rows, issued_at="2026-10-17T15:00:00-03:00") == 1


def test_floor_issue_time_uses_the_manifest_cadence():
    assert swxkit.floor_issue_time(PID, ISSUED + dt.timedelta(minutes=59, seconds=59)) == ISSUED


@pytest.mark.django_db
def test_partition_is_created_on_issued_at():
    from django.db import connection
    _issue()
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('core_forecast_y2026m10') IS NOT NULL")
        assert cursor.fetchone()[0]


def test_ensure_partition_refuses_unknown_tables():
    with pytest.raises(ValueError):
        swxkit._ensure_partition("core_product; DROP TABLE x", ISSUED)


@pytest.mark.django_db
def test_read_series_returns_numeric_values_oldest_first():
    t0 = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
    swxkit.publish_timeseries("noaa.kp", [
        {"time_tag": t0, "kp": 2.33}, {"time_tag": t0 + dt.timedelta(hours=3), "kp": ""},
        {"time_tag": t0 + dt.timedelta(hours=6), "kp": 4}, {"time_tag": t0 + dt.timedelta(hours=9), "kp": 5.0},
    ])
    out = swxkit.read_series("noaa.kp", "kp", t0, until=t0 + dt.timedelta(hours=9))
    assert out == [(t0, 2.33), (t0 + dt.timedelta(hours=6), 4.0)]
