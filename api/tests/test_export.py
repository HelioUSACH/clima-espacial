"""The shared export layer (D-084, D-087)."""

import copy
import datetime as dt
import json
from pathlib import Path

import pytest
from django.test import RequestFactory

from api import export
from core import swxkit
from core.manifests.loader import (
    ManifestError,
    discover_manifests,
    license_allows_redistribution,
    load_manifest,
)
from modules.swpc import adapters

SWPC_FIXTURES = Path(__file__).resolve().parents[2] / "modules" / "swpc" / "tests" / "fixtures"
rf = RequestFactory()


def _publish_fixture(product_id, filename, parse):
    records = json.loads((SWPC_FIXTURES / filename).read_text())
    swxkit.publish_timeseries(product_id, parse(records))
    return records


# --- what is exportable ------------------------------------------------------------

def test_exportable_datasets_come_from_the_manifests():
    assert [d.id for d in export.datasets()] == [
        "noaa.goes.protons", "noaa.goes.xray", "noaa.kp", "noaa.rtsw.mag", "noaa.rtsw.wind"]


def test_no_exported_product_has_a_licence_that_forbids_redistribution():
    # D-078: if someone sets exchange.hapi: true on a non-commercial product
    # (Kyoto's Dst/AE), this fails -- and so does validate_manifests.
    for product_id, manifest in discover_manifests().items():
        if manifest.get("exchange", {}).get("hapi"):
            assert license_allows_redistribution(manifest["source"].get("license", "")), product_id
    for kyoto in ("kyoto.dst", "kyoto.ae"):
        assert export.get_dataset(kyoto) is None
        assert kyoto in [m["id"] for m in export.not_exported()]


@pytest.mark.parametrize("text,ok", [
    ("Public domain (NOAA)", True), ("CC-BY-4.0", True), ("CC BY 4.0", True), ("CC0-1.0", True),
    ("Non-commercial use; WDC Kyoto data", False), ("Non-commercial use; quicklook data (WDC Kyoto)", False),
    ("CC-BY-NC-4.0", False), ("", False), ("Proprietary", False),
])
def test_licence_rule(text, ok):
    assert license_allows_redistribution(text) is ok


def test_validate_manifests_refuses_hapi_on_a_non_commercial_product(tmp_path):
    source = Path(__file__).resolve().parents[2] / "modules" / "kyoto" / "manifests" / "dst.yaml"
    text = source.read_text().replace("exchange: {hapi: false, feed: false}", "exchange: {hapi: true, feed: false}")
    assert "hapi: true" in text
    path = tmp_path / "dst.yaml"
    path.write_text(text)
    with pytest.raises(ManifestError, match="doesn't allow redistribution"):
        load_manifest(path)


def test_own_and_relay_come_from_the_provider():
    assert all(d.relay and not d.own for d in export.datasets())  # all NOAA today
    mag = export.get_dataset("noaa.rtsw.mag")
    assert mag.provider_url == "https://www.spaceweather.gov/products/solar-wind"


@pytest.mark.django_db
def test_withdrawing_is_only_the_manifest_flag(monkeypatch):
    manifests = copy.deepcopy(discover_manifests())
    manifests["noaa.kp"]["exchange"]["hapi"] = False
    monkeypatch.setattr(export, "discover_manifests", lambda: manifests)
    assert export.get_dataset("noaa.kp") is None
    assert "noaa.kp" in [m["id"] for m in export.not_exported()]


# --- columns and rows -----------------------------------------------------------------

def test_long_format_goes_pivots_energy_into_columns():
    xray = export.get_dataset("noaa.goes.xray")
    assert xray.channel_key == "energy"
    assert [c.name for c in xray.columns] == ["flux_0.05-0.4nm", "flux_0.1-0.8nm", "satellite"]
    assert [c.type for c in xray.columns] == ["double", "double", "string"]
    protons = export.get_dataset("noaa.goes.protons")
    assert [c.name for c in protons.columns][:3] == ["flux_ge1MeV", "flux_ge5MeV", "flux_ge10MeV"]
    assert protons.column("flux_ge10MeV").channel == ">=10 MeV"
    assert protons.column("flux_ge10MeV").units == "1/(cm2 s sr)"


@pytest.mark.django_db
def test_goes_rows_from_a_real_payload_one_row_per_time(settings):
    records = _publish_fixture("noaa.goes.xray", "xray_flux_1_day.json", adapters.parse_goes_xray)
    ds = export.get_dataset("noaa.goes.xray")
    times = sorted({r["time_tag"] for r in records})
    start = export.parse_time(times[0])
    stop = export.parse_time(times[-1]) + dt.timedelta(minutes=1)
    assert export.count_rows(ds, start, stop) == len(times)
    rows = list(export.iter_rows(ds, start, stop, ds.columns))
    assert [export.fmt_time(t) for t, _v in rows] == times
    first = {r["energy"]: r["flux"] for r in records if r["time_tag"] == times[0]}
    assert rows[0][1] == [first["0.05-0.4nm"], first["0.1-0.8nm"], "18"]


@pytest.mark.django_db
def test_one_column_products_and_fill(recent_mag):
    end = recent_mag
    swxkit.publish_timeseries("noaa.rtsw.mag", [{"time_tag": end + dt.timedelta(minutes=1), "bt": None,
                                                 "bx_gsm": -9999.0, "by_gsm": "", "bz_gsm": 2.5}])
    ds = export.get_dataset("noaa.rtsw.mag")
    rows = list(export.iter_rows(ds, end, end + dt.timedelta(minutes=2), ds.columns))
    assert rows[-1][1] == [None, None, None, 2.5]
    assert [export.fmt_value(v) for v in rows[-1][1]] == ["-1.0E31", "-1.0E31", "-1.0E31", "2.5"]


@pytest.mark.django_db
def test_span_cap_depends_on_the_data_cadence(recent_mag, settings):
    mag = export.get_dataset("noaa.rtsw.mag")
    assert export.native_step_seconds(mag) == 60
    stop = recent_mag
    export.check_span(mag, stop - dt.timedelta(days=31), stop)
    with pytest.raises(export.ExportError) as exc:
        export.check_span(mag, stop - dt.timedelta(days=31, seconds=1), stop)
    assert exc.value.code == 1408 and "31 days" in exc.value.message
    with pytest.raises(export.ExportError) as exc:
        export.check_span(mag, stop, stop)
    assert exc.value.code == 1404
    # 3-hourly Kp: a year per request.
    t0 = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
    swxkit.publish_timeseries("noaa.kp", [{"time_tag": t0 + dt.timedelta(hours=3 * i), "kp": 2.0, "a_running": 7,
                                           "station_count": 8} for i in range(20)])
    kp = export.get_dataset("noaa.kp")
    assert export.native_step_seconds(kp) == 3 * 3600
    export.check_span(kp, t0, t0 + dt.timedelta(days=366))


@pytest.mark.django_db
def test_row_cap(settings, recent_mag):
    settings.SWX_EXPORT_MAX_ROWS = 100
    mag = export.get_dataset("noaa.rtsw.mag")
    export.check_rows(mag, 100)
    with pytest.raises(export.ExportError) as exc:
        export.check_rows(mag, 101)
    assert exc.value.code == 1408


@pytest.mark.parametrize("raw,expected", [
    ("2026-10-17T12:34:56Z", dt.datetime(2026, 10, 17, 12, 34, 56, tzinfo=dt.UTC)),
    ("2026-10-17T12:34:56.250Z", dt.datetime(2026, 10, 17, 12, 34, 56, 250000, tzinfo=dt.UTC)),
    ("2026-10-17T12Z", dt.datetime(2026, 10, 17, 12, tzinfo=dt.UTC)),
    ("2026-10-17", dt.datetime(2026, 10, 17, tzinfo=dt.UTC)),
    ("2026-10-17Z", dt.datetime(2026, 10, 17, tzinfo=dt.UTC)),
    ("2026-290T00:00Z", dt.datetime(2026, 10, 17, tzinfo=dt.UTC)),
    ("2026-10Z", dt.datetime(2026, 10, 1, tzinfo=dt.UTC)),
    ("2026-10-17T09:00:00-03:00", dt.datetime(2026, 10, 17, 12, tzinfo=dt.UTC)),
])
def test_parse_time(raw, expected):
    assert export.parse_time(raw) == expected


@pytest.mark.parametrize("raw", ["", "yesterday", "2026-13-01", "2026-400Z", "2026-10-17T25:00Z"])
def test_parse_time_rejects(raw):
    with pytest.raises(ValueError):
        export.parse_time(raw)


def test_iso_duration():
    assert [export.iso_duration(s) for s in (60, 300, 3600, 10800, 86400, 90)] == [
        "PT1M", "PT5M", "PT1H", "PT3H", "P1D", "PT1M30S"]


# --- the CSV header block --------------------------------------------------------

@pytest.mark.django_db
def test_csv_header_block_for_a_relay_dataset():
    ds = export.get_dataset("noaa.kp")
    lines = export.csv_header_lines(rf.get("/"), ds, ds.columns,
                                    retrieved_at=dt.datetime(2026, 10, 18, 20, tzinfo=dt.UTC))
    assert all(line.startswith("# ") for line in lines)
    text = "\n".join(lines)
    assert "# Planetary Kp index / Índice planetario Kp" in text
    assert "# Product page: http://testserver/productos/noaa.kp/" in text
    assert "# Source: NOAA SWPC (https://www.spaceweather.gov/products/planetary-k-index)" in text
    assert "# Licence: Public domain (NOAA)" in text
    assert "offered for convenience and may be withdrawn" in text
    assert "# Retrieved: 2026-10-18T20:00:00Z" in text
    assert "# Columns: kp [dimensionless]; a_running [dimensionless]; station_count [dimensionless]" in text


@pytest.mark.django_db
def test_own_product_exports_without_code_changes(own_product):
    manifest, end = own_product
    ds = export.get_dataset("usach.test.own")
    assert ds is not None and ds.own and not ds.relay
    assert [c.name for c in ds.columns] == ["dbdt", "probability"]
    rows = list(export.iter_rows(ds, end - dt.timedelta(minutes=2), end + dt.timedelta(minutes=1), ds.columns))
    assert [v for _t, v in rows] == [[3.0, 0.25], [1.5, 0.25], [0.0, 0.25]]
    lines = export.csv_header_lines(rf.get("/"), ds, ds.columns, retrieved_at=end)
    text = "\n".join(lines)
    assert "convenience" not in text
    assert "# Citation: HelioUSACH (2026), test product, doi:10.0000/test" in text
    assert "# Licence: CC-BY-4.0" in text
