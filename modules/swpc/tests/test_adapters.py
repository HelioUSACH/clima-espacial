import datetime as dt
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


# D-092: what NOAA's integral-electrons files really carried on 2026-10-18
# (the 1- and 7-day fixtures are trimmed copies of that day's live files).
def test_parse_goes_electrons_has_only_the_2mev_channel():
    for name in ("integral_electrons_1_day.json", "integral_electrons_7_day.json"):
        raw = _load(name)
        rows = adapters.parse_goes_electrons(raw)
        assert len(rows) == len(raw)
        assert {r["energy"] for r in rows} == {">=2 MeV"}
        assert all({"time_tag", "flux", "energy", "satellite"} == set(r) for r in rows)
        assert len({r["time_tag"] for r in rows}) == len(rows)  # one row per time tag


def test_parse_goes_electron_fluence_keeps_completed_days_only():
    raw = _load("integral_electron_fluence_7_day.json")
    # Midday on the file's last day: that day isn't complete, so it is dropped.
    now = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.UTC)
    rows = adapters.parse_goes_electron_fluence(raw, now)
    assert [r["time_tag"] for r in rows] == [f"2026-10-{d}T00:00:00Z" for d in range(13, 18)]
    assert all(set(r) == {"time_tag", "fluence", "satellite"} for r in rows)
    assert rows[0]["fluence"] == raw[0]["fluence"]
    # Once 2026-10-18 has ended, it is published too.
    later = adapters.parse_goes_electron_fluence(raw, dt.datetime(2026, 10, 19, 0, 0, tzinfo=dt.UTC))
    assert len(later) == 6
    # Another energy, if NOAA ever adds one, doesn't make two rows per day.
    extra = raw + [{**raw[0], "energy": ">=0.8 MeV", "fluence": 1.0}]
    assert len(adapters.parse_goes_electron_fluence(extra, now)) == 5
