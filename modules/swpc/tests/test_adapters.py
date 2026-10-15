import json
from pathlib import Path

from modules.swpc import adapters

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def test_parse_rtsw_mag_keeps_only_active_rows_and_stores_source():
    raw = _load("rtsw_mag_1m.json")
    rows = adapters.parse_rtsw_mag(raw)
    assert rows, "fixture should contain at least one active row"
    assert len(rows) == sum(1 for r in raw if r["active"])
    for row in rows:
        assert row["source"]  # spacecraft name kept as data (D-040), never dropped
        assert {"time_tag", "source", "bt", "bx_gsm", "by_gsm", "bz_gsm"} == set(row)


def test_parse_rtsw_wind_keeps_only_active_rows():
    raw = _load("rtsw_wind_1m.json")
    rows = adapters.parse_rtsw_wind(raw)
    assert rows
    assert len(rows) == sum(1 for r in raw if r["active"])
    for row in rows:
        assert {"time_tag", "source", "proton_speed", "proton_density", "proton_temperature"} == set(row)


def test_parse_kp():
    raw = _load("planetary_k_index.json")
    rows = adapters.parse_kp(raw)
    assert len(rows) == len(raw)
    assert all({"time_tag", "kp", "a_running", "station_count"} == set(r) for r in rows)
    assert all(0 <= r["kp"] <= 9 for r in rows)


def test_parse_dst():
    raw = _load("kyoto_dst.json")
    rows = adapters.parse_dst(raw)
    assert len(rows) == len(raw)
    assert all({"time_tag", "dst"} == set(r) for r in rows)


def test_parse_goes_xray_covers_both_energy_channels():
    raw = _load("xray_flux_1_day.json")
    rows = adapters.parse_goes_xray(raw)
    assert len(rows) == len(raw)
    energies = {r["energy"] for r in rows}
    assert energies == {"0.05-0.4nm", "0.1-0.8nm"}


def test_parse_goes_protons_covers_all_energy_channels():
    raw = _load("integral_protons_1_day.json")
    rows = adapters.parse_goes_protons(raw)
    assert len(rows) == len(raw)
    energies = {r["energy"] for r in rows}
    assert len(energies) == 8  # GOES integral-proton channel count, see manifest


def test_parse_scales_reads_today_entry():
    raw = _load("scales.json")
    parsed = adapters.parse_scales(raw)
    assert parsed["issued"].startswith(raw["0"]["DateStamp"])
    assert parsed["payload"]["days"] == raw
