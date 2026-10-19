"""D-091: `manage.py export_nrt` -- day files, the marker, late rows and
revisions, deterministic output, permissions, retention, forecasts."""

import datetime as dt
import gzip
import json
import os
import stat

import pytest
from django.core.management import call_command
from django.utils import timezone

from core import swxkit
from core.management.commands import export_nrt
from core.models import Event, Forecast, TimeSeriesPoint

DAY0 = dt.datetime(2026, 10, 10, tzinfo=dt.UTC)


def _kp_rows(days=3):
    return [{"time_tag": DAY0 + dt.timedelta(hours=3 * i), "kp": (i % 9) * 1.0, "a_running": i, "station_count": 8}
            for i in range(8 * days)]


def _read(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def _files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.jsonl.gz"))


def _backdate(model, field, when):
    model.objects.all().update(**{field: when})


@pytest.fixture
def kp(db):
    swxkit.publish_timeseries("noaa.kp", _kp_rows())


@pytest.mark.django_db
def test_first_run_writes_every_day_with_the_stored_fields(tmp_path, kp):
    result = export_nrt.export(tmp_path, keep_days=30)
    assert result["since"] is None and result["written"] == 3
    assert _files(tmp_path) == [f"noaa.kp/2026/noaa.kp_202610{d}.jsonl.gz" for d in (10, 11, 12)]
    rows = _read(tmp_path / "noaa.kp/2026/noaa.kp_20261010.jsonl.gz")
    assert len(rows) == 8
    assert list(rows[0]) == sorted(["time_tag", "source", "payload", "quality", "provenance", "inserted_at",
                                    "revised_at"])
    assert rows[0]["time_tag"] == "2026-10-10T00:00:00Z"
    assert rows[0]["payload"] == {"kp": 0.0, "a_running": 0, "station_count": 8}
    assert rows[0]["revised_at"] is None and rows[0]["inserted_at"].endswith("Z")
    assert [r["time_tag"] for r in rows] == sorted(r["time_tag"] for r in rows)
    marker = json.loads((tmp_path / ".export_nrt.json").read_text())
    assert marker["files_written"] == 3


@pytest.mark.django_db
def test_only_days_with_rows_newer_than_the_marker_are_rewritten(tmp_path, kp):
    long_ago = timezone.now() - dt.timedelta(days=5)
    _backdate(TimeSeriesPoint, "inserted_at", long_ago)
    export_nrt.export(tmp_path, keep_days=30)
    assert export_nrt.export(tmp_path, keep_days=30)["written"] == 0  # nothing changed since

    # A revision (D-080) on day 11 re-exports day 11 only.
    point = TimeSeriesPoint.objects.get(product_id="noaa.kp", time_tag=DAY0 + dt.timedelta(days=1, hours=6))
    TimeSeriesPoint.objects.filter(pk=point.pk, time_tag=point.time_tag).update(
        payload={**point.payload, "kp": 8.0}, revised_at=timezone.now())
    result = export_nrt.export(tmp_path, keep_days=30)
    assert (result["written"], result["unchanged"], result["per_product"]) == (1, 0, {"noaa.kp": 1})
    revised = [r for r in _read(tmp_path / "noaa.kp/2026/noaa.kp_20261011.jsonl.gz") if r["revised_at"]]
    assert len(revised) == 1 and revised[0]["payload"]["kp"] == 8.0


@pytest.mark.django_db
def test_a_late_row_for_an_old_day_reexports_that_day(tmp_path, kp):
    _backdate(TimeSeriesPoint, "inserted_at", timezone.now() - dt.timedelta(days=5))
    export_nrt.export(tmp_path, keep_days=30)
    # Kyoto-style late data: a value for a day weeks back, stored today.
    old = DAY0 - dt.timedelta(days=20)
    swxkit._sync_product("kyoto.ae")
    swxkit._ensure_partition("core_timeseries", old)
    TimeSeriesPoint.objects.create(product_id="kyoto.ae", time_tag=old, source="KYOTO-HAPI",
                                   payload={"ae": 100, "al": -60, "au": 40, "ao": -10, "version": 10})
    result = export_nrt.export(tmp_path, keep_days=30)
    assert result["per_product"] == {"kyoto.ae": 1} and result["written"] == 1
    assert (tmp_path / "kyoto.ae/2026/kyoto.ae_20260920.jsonl.gz").exists()


@pytest.mark.django_db
def test_rows_inserted_just_before_the_run_are_caught_next_time(tmp_path, kp):
    # The marker is the run's start minus an hour (a row stamped before the
    # run but committed after it must not be lost).
    run_at = timezone.now()
    export_nrt.export(tmp_path, keep_days=30, now=run_at)
    _backdate(TimeSeriesPoint, "inserted_at", run_at - dt.timedelta(minutes=30))
    result = export_nrt.export(tmp_path, keep_days=30)
    assert result["since"] == run_at - export_nrt.OVERLAP
    assert result["written"] == 3  # their inserted_at changed, so did the files


@pytest.mark.django_db
def test_output_is_deterministic_and_an_unchanged_day_is_left_alone(tmp_path, kp):
    a, b = tmp_path / "a", tmp_path / "b"
    export_nrt.export(a, keep_days=30)
    export_nrt.export(b, keep_days=30, full=True)
    for name in _files(a):
        assert (a / name).read_bytes() == (b / name).read_bytes()
        assert (a / name).read_bytes()[4:8] == b"\0\0\0\0"  # no gzip timestamp
    path = a / "noaa.kp/2026/noaa.kp_20261010.jsonl.gz"
    os.utime(path, (1_000_000_000, 1_000_000_000))
    result = export_nrt.export(a, keep_days=0, full=True)
    assert result["unchanged"] == 3 and result["written"] == 0
    assert path.stat().st_mtime == 1_000_000_000  # not rewritten
    assert not list(a.rglob("*.tmp"))


@pytest.mark.django_db
def test_files_are_valid_gzip_and_not_world_readable(tmp_path, kp):
    export_nrt.export(tmp_path, keep_days=30)
    for path in tmp_path.rglob("*.jsonl.gz"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
        with gzip.open(path) as fh:
            assert fh.read().endswith(b"\n")
    assert stat.S_IMODE((tmp_path / ".export_nrt.json").stat().st_mode) == 0o640
    for path in [p for p in tmp_path.rglob("*") if p.is_dir()]:
        assert not stat.S_IMODE(path.stat().st_mode) & 0o007


@pytest.mark.django_db
def test_retention_deletes_old_files_tmp_leftovers_and_empty_dirs_only(tmp_path, kp):
    export_nrt.export(tmp_path, keep_days=30)
    old = tmp_path / "noaa.kp/2026/noaa.kp_20261010.jsonl.gz"
    stale_tmp = tmp_path / "noaa.kp/2026/noaa.kp_20261011.jsonl.gz.tmp"
    stale_tmp.write_bytes(b"partial")
    gone_dir = tmp_path / "noaa.dst/2025"
    gone_dir.mkdir(parents=True)
    (gone_dir / "noaa.dst_20251001.jsonl.gz").write_bytes(b"x")
    month_ago = timezone.now().timestamp() - 31 * 86400
    for path in (old, stale_tmp, gone_dir / "noaa.dst_20251001.jsonl.gz", tmp_path / ".export_nrt.json"):
        os.utime(path, (month_ago, month_ago))
    deleted = export_nrt.prune(tmp_path, 30, timezone.now())
    assert deleted == 2
    assert not old.exists() and not stale_tmp.exists() and not (tmp_path / "noaa.dst").exists()
    assert (tmp_path / "noaa.kp/2026/noaa.kp_20261011.jsonl.gz").exists()
    assert (tmp_path / ".export_nrt.json").exists()  # the marker is never pruned


@pytest.mark.django_db
def test_forecast_and_event_products(tmp_path):
    issued = dt.datetime(2026, 10, 17, 21, 0, tzinfo=dt.UTC)
    swxkit.publish_forecast("usach.kp.persistence", issued_at=issued, model_version="0.1.0",
                            provenance={"inputs": [], "producer": "test"},
                            rows=[{"valid_at": issued + dt.timedelta(hours=h), "kp": {"value": 3.0, "lower": 2.0,
                                                                                         "upper": 4.0}}
                                  for h in (3, 6, 12, 24)])
    swxkit.publish_event("noaa.scales", {"days": {}}, valid_from=issued, issued=issued)
    export_nrt.export(tmp_path, keep_days=30)
    # An issue stays in its issue day's file, even with valid times the day after.
    rows = _read(tmp_path / "usach.kp.persistence/2026/usach.kp.persistence_20261017.jsonl.gz")
    assert [r["lead_seconds"] for r in rows] == [10800, 21600, 43200, 86400]
    assert set(rows[0]) == {"model_version", "issued_at", "valid_at", "lead_seconds", "site", "values",
                            "provenance", "inserted_at"}
    assert rows[0]["values"] == {"kp": {"value": 3.0, "lower": 2.0, "upper": 4.0}}
    assert not (tmp_path / "usach.kp.persistence/2026/usach.kp.persistence_20261018.jsonl.gz").exists()
    events = _read(tmp_path / "noaa.scales/2026/noaa.scales_20261017.jsonl.gz")
    assert events[0]["issued"] == "2026-10-17T21:00:00Z" and "created_at" in events[0]
    # Later issues re-export only their own day.
    _backdate(Forecast, "inserted_at", timezone.now() - dt.timedelta(days=2))
    _backdate(Event, "created_at", timezone.now() - dt.timedelta(days=2))
    export_nrt.export(tmp_path, keep_days=30)
    assert export_nrt.export(tmp_path, keep_days=30)["written"] == 0


@pytest.mark.django_db
def test_command(tmp_path, kp, capsys, settings):
    settings.SWX_NRT_EXPORT_DIR = tmp_path / "nrt"
    call_command("export_nrt")
    out = capsys.readouterr().out
    assert "first run" in out and "noaa.kp: 3 day file(s) written" in out
    call_command("export_nrt", "--keep-days", "0")
    assert "0 written, 3 unchanged" in capsys.readouterr().out
