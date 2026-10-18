"""Pure parsing: Kyoto HAPI CSV (+ its `info` JSON) -> rows for
`core.swxkit.publish_timeseries`. No network I/O, so every rule is tested
against recorded fixtures (tests/fixtures/).

- **Columns** come from `info["parameters"]`, in order: HAPI's CSV has no
  header row. `NAMES` maps Kyoto's names to ours.
- **Time:** Kyoto stamps the *centre* of each bin (hourly Dst at hh:29:30).
  The portal's convention is the interval *start* (noaa.kp, D-074), so each
  stamp is floored to the dataset's cadence: hh:29:30 -> hh:00:00. A
  1-minute stamp is already on its minute and stays as is.
- **Fill** (`99999`, from `info`) becomes null; a row with no value left is
  dropped.
- **version** is `versionCode` (Mm: M 0 realtime, 1 provisional, 2 final),
  an integer, so a later processing level compares higher (D-080).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re

NAMES = {
    "dstValue": "dst",
    "aeValue": "ae", "alValue": "al", "auValue": "au", "aoValue": "ao",
    "versionCode": "version",
}
SOURCE_KYOTO = "KYOTO-HAPI"
SOURCE_NOAA = "NOAA-MIRROR"
# A NOAA mirror value has no Kyoto version; below realtime (0), so any
# Kyoto value for the same hour replaces it (D-081).
NOAA_VERSION = -1

_ISO_DURATION = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def cadence_seconds(duration: str) -> int:
    match = _ISO_DURATION.match(duration or "")
    if not match or not any(match.groups()):
        raise ValueError(f"unsupported HAPI cadence {duration!r}")
    hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _floor(when: dt.datetime, seconds: int) -> dt.datetime:
    epoch = int(when.timestamp())
    return dt.datetime.fromtimestamp(epoch - epoch % seconds, tz=dt.UTC)


def parse_csv(info: dict, text: str) -> list[dict]:
    params = info["parameters"]
    step = cadence_seconds(info["cadence"])
    fills = {p["name"]: p.get("fill") for p in params}
    rows = []
    for record in csv.reader(io.StringIO(text)):
        if not record or record[0].startswith("#"):
            continue
        if len(record) != len(params):
            raise ValueError(f"expected {len(params)} columns, got {len(record)}: {record!r}")
        stamp = dt.datetime.fromisoformat(record[0].replace("Z", "+00:00"))
        row = {"time_tag": _floor(stamp, step), "source": SOURCE_KYOTO}
        measured = False
        for param, raw in zip(params[1:], record[1:], strict=True):
            name = NAMES.get(param["name"], param["name"])
            value = None if raw == "" or raw == fills[param["name"]] else int(raw)
            row[name] = value
            if name != "version" and value is not None:
                measured = True
        if measured:
            rows.append(row)
    return rows


def parse_noaa_mirror(records: list[dict]) -> list[dict]:
    """NOAA SWPC's `products/kyoto-dst.json` (the fallback route): hourly,
    `time_tag` already the hour's start (naive UTC)."""
    return [{"time_tag": r["time_tag"] + "Z" if not r["time_tag"].endswith("Z") else r["time_tag"],
             "source": SOURCE_NOAA, "dst": r["dst"], "version": NOAA_VERSION}
            for r in records if isinstance(r.get("dst"), (int, float))]
