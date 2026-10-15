"""Pure parsing functions: raw provider JSON -> rows ready for
`core.swxkit.publish_timeseries`/`publish_event`. No network I/O here on
purpose, so every adapter is testable against a recorded fixture
(tests/fixtures/, tests/test_adapters.py) without a live source.

Shared rule (docs/DECISIONS.md, "never use the frozen products/solar-wind/
mag-*.json / plasma-*.json" — SCN 26-21): RTSW payloads carry several
sources (SOLAR1, DSCOVR, ACE, …) per timestamp, only one of which is
`active`. We keep `active == true` rows only and store the winning
`source` as plain data — never hard-code which spacecraft is primary
(D-040)."""

from __future__ import annotations


def _active_rows(records: list[dict]) -> list[dict]:
    return [r for r in records if r.get("active")]


def parse_rtsw_mag(records: list[dict]) -> list[dict]:
    return [
        {
            "time_tag": r["time_tag"],
            "source": r["source"],
            "bt": r["bt"],
            "bx_gsm": r["bx_gsm"],
            "by_gsm": r["by_gsm"],
            "bz_gsm": r["bz_gsm"],
        }
        for r in _active_rows(records)
    ]


def parse_rtsw_wind(records: list[dict]) -> list[dict]:
    return [
        {
            "time_tag": r["time_tag"],
            "source": r["source"],
            "proton_speed": r["proton_speed"],
            "proton_density": r["proton_density"],
            "proton_temperature": r["proton_temperature"],
        }
        for r in _active_rows(records)
    ]


def parse_kp(records: list[dict]) -> list[dict]:
    return [
        {
            "time_tag": r["time_tag"],
            "kp": r["Kp"],
            "a_running": r["a_running"],
            "station_count": r["station_count"],
        }
        for r in records
    ]


def parse_dst(records: list[dict]) -> list[dict]:
    return [{"time_tag": r["time_tag"], "dst": r["dst"]} for r in records]


def parse_goes_xray(records: list[dict]) -> list[dict]:
    return [
        {
            "time_tag": r["time_tag"],
            "flux": r["flux"],
            "energy": r["energy"],
            "satellite": r["satellite"],
        }
        for r in records
    ]


def parse_goes_protons(records: list[dict]) -> list[dict]:
    return [
        {
            "time_tag": r["time_tag"],
            "flux": r["flux"],
            "energy": r["energy"],
            "satellite": r["satellite"],
        }
        for r in records
    ]


def parse_scales(data: dict) -> dict:
    """`data` is the `{"−1": {...}, "0": {...}, ...}` mapping keyed by day
    offset from today. Returned as one event payload + its issue time (the
    "0" / today entry's DateStamp+TimeStamp, UTC)."""
    today = data["0"]
    issued = f"{today['DateStamp']}T{today['TimeStamp']}Z"
    return {"issued": issued, "payload": {"days": data}}
