"""D-080: revisable series in `publish_timeseries` (`revise` in the manifest)."""

import copy
import datetime as dt

import pytest
import yaml

from core import swxkit
from core.manifests.loader import ManifestError, get_manifest, load_manifest
from core.models import Heartbeat, TimeSeriesPoint

PID = "noaa.kp"  # served below as a revisable variant
T = dt.datetime(2026, 10, 17, 18, tzinfo=dt.UTC)
H = dt.timedelta(hours=1)


@pytest.fixture
def revisable(monkeypatch):
    real = get_manifest

    def apply(**revise):
        manifest = copy.deepcopy(real(PID))
        manifest["revise"] = revise or {"window": "6h"}
        if "version_field" in manifest["revise"]:
            manifest["parameters"].append({"name": manifest["revise"]["version_field"]})
        monkeypatch.setattr(swxkit, "get_manifest", lambda pid: copy.deepcopy(manifest) if pid == PID else real(pid))
    return apply


def _stored(time_tag):
    return TimeSeriesPoint.objects.get(product_id=PID, time_tag=time_tag)


@pytest.mark.django_db
def test_a_growing_in_progress_hour_ends_at_its_final_value(revisable):
    revisable()
    # NOAA's Dst mirror at 18:35 shows the 18:00 hour so far (-6), at 19:35 its full value (-8).
    assert swxkit.publish_timeseries(PID, [{"time_tag": T - H, "kp": -3}, {"time_tag": T, "kp": -6}]) == 2
    assert _stored(T).revised_at is None
    assert swxkit.publish_timeseries(PID, [{"time_tag": T - H, "kp": -3}, {"time_tag": T, "kp": -8},
                                           {"time_tag": T + H, "kp": -12}]) == 2
    point = _stored(T)
    assert point.payload == {"kp": -8}
    assert point.revised_at is not None
    assert _stored(T - H).revised_at is None  # identical: a no-op
    assert TimeSeriesPoint.objects.filter(product_id=PID).count() == 3
    assert Heartbeat.objects.get(product_id=PID).detail == "1 new, 1 revised of 3 fetched"


@pytest.mark.django_db
def test_outside_the_window_nothing_changes(revisable):
    revisable(window="2h")
    swxkit.publish_timeseries(PID, [{"time_tag": T + i * H, "kp": i} for i in range(5)])  # latest T+4h
    assert swxkit.publish_timeseries(PID, [{"time_tag": T + H, "kp": 99}, {"time_tag": T + 2 * H, "kp": 98}]) == 1
    assert _stored(T + H).payload == {"kp": 1}       # before latest - 2h
    assert _stored(T + 2 * H).payload == {"kp": 98}  # exactly at latest - 2h: revisable


@pytest.mark.django_db
def test_a_hole_inside_the_window_is_filled(revisable):
    revisable()
    swxkit.publish_timeseries(PID, [{"time_tag": T, "kp": 1}, {"time_tag": T + 2 * H, "kp": 3}])
    assert swxkit.publish_timeseries(PID, [{"time_tag": T + H, "kp": 2}]) == 1
    assert _stored(T + H).revised_at is None


@pytest.mark.django_db
def test_a_lower_version_never_overwrites_a_higher_one(revisable):
    revisable(window="6h", version_field="version")
    swxkit.publish_timeseries(PID, [{"time_tag": T, "kp": -10, "version": 0, "source": "KYOTO-HAPI"}])
    # The NOAA mirror (version -1) disagrees: ignored.
    assert swxkit.publish_timeseries(PID, [{"time_tag": T, "kp": -6, "version": -1, "source": "NOAA-MIRROR"}]) == 0
    assert _stored(T).payload["kp"] == -10
    # Kyoto's provisional value (10) wins; then a stale realtime (0) can't undo it.
    assert swxkit.publish_timeseries(PID, [{"time_tag": T, "kp": -11, "version": 10}]) == 1
    assert swxkit.publish_timeseries(PID, [{"time_tag": T, "kp": -10, "version": 0}]) == 0
    point = _stored(T)
    assert point.payload == {"kp": -11, "version": 10} and point.source == ""


@pytest.mark.django_db
def test_same_version_with_a_new_value_is_a_revision(revisable):
    revisable(window="6h", version_field="version")
    swxkit.publish_timeseries(PID, [{"time_tag": T, "kp": -6, "version": 0}])
    assert swxkit.publish_timeseries(PID, [{"time_tag": T, "kp": -8, "version": 0}]) == 1


@pytest.mark.django_db
def test_two_rows_for_one_time_tag_are_refused(revisable):
    revisable()
    with pytest.raises(swxkit.PublishError, match="two rows for time_tag"):
        swxkit.publish_timeseries(PID, [{"time_tag": T, "kp": 1}, {"time_tag": T, "kp": 2}])
    assert not TimeSeriesPoint.objects.filter(product_id=PID).exists()


@pytest.mark.django_db
def test_revise_since_widens_the_window_for_a_backfill(revisable):
    revisable(window="1h")
    swxkit.publish_timeseries(PID, [{"time_tag": T + i * H, "kp": i} for i in range(10)])
    assert swxkit.publish_timeseries(PID, [{"time_tag": T, "kp": 50}]) == 0
    assert swxkit.publish_timeseries(PID, [{"time_tag": T, "kp": 50}], revise_since=T) == 1
    assert _stored(T).payload == {"kp": 50}


@pytest.mark.django_db
def test_revise_since_needs_a_revisable_product():
    with pytest.raises(swxkit.PublishError, match="revise_since"):
        swxkit.publish_timeseries(PID, [{"time_tag": T, "kp": 1}], revise_since=T)


@pytest.mark.django_db
def test_non_revisable_products_still_skip_old_rows():
    swxkit.publish_timeseries(PID, [{"time_tag": T, "kp": 1}, {"time_tag": T + H, "kp": 2}])
    assert swxkit.publish_timeseries(PID, [{"time_tag": T + H, "kp": 9}]) == 0
    assert _stored(T + H).payload == {"kp": 2} and _stored(T + H).revised_at is None


def _write(tmp_path, data):
    path = tmp_path / "modules" / "demo" / "manifests" / "p.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))
    return path


BASE = {
    "id": "demo.series", "version": 1, "kind": "timeseries", "title": {"es": "S", "en": "S"},
    "maturity": "experimental", "cadence": "1m", "stale_after": "10m", "producer": {"type": "collector"},
    "source": {"provider": "Test"}, "parameters": [{"name": "flux"}, {"name": "version"}],
}


@pytest.mark.parametrize("revise, extra_params, fragment", [
    ({"window": "6 hours"}, [], "does not match"),
    ({}, [], "'window' is a required property"),
    ({"window": "6h", "version_field": "nope"}, [], "is not a parameter"),
    ({"window": "6h"}, [{"name": "energy"}], "needs revise.key: [energy]"),
    ({"window": "6h", "key": ["energy"]}, [{"name": "energy"}], "isn't supported yet"),
])
def test_manifest_rejects_bad_revise(tmp_path, revise, extra_params, fragment):
    data = copy.deepcopy(BASE)
    data["revise"] = revise
    data["parameters"] += extra_params
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, data))
    assert any(fragment in e for e in exc.value.errors), exc.value.errors


def test_manifest_accepts_revise(tmp_path):
    data = dict(copy.deepcopy(BASE), revise={"window": "6h", "version_field": "version"})
    assert load_manifest(_write(tmp_path, data))["revise"]["window"] == "6h"
