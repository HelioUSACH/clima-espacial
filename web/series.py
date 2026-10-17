"""`serie.json`'s data: bucketed in SQL, never row-by-row in Python (D-056).

One query per (product, range). PostgreSQL's `date_bin` groups the rows into
fixed buckets (`web/charts.py` `BUCKET_SECONDS`), and each plotted field
comes back as mean/min/max per bucket. Only the needed JSONB keys are
extracted (`payload -> 'bt'`, never the whole payload), so a 7-day click
returns ~670 buckets per series instead of decoding ~10,000 full 1-min
payloads in Python (16-PERF §1).

Response contract (D-056):

    {"time": [t0, ...],                  # bucket start, UNIX seconds, UTC
     "series": {field: [mean, ...]},     # the plotted line
     "min": {field: [...]}, "max": {field: [...]},
     "source": [last non-empty source in the bucket, or null, ...],
     "bucket_seconds": N}

A bucket with no valid value is `null` in every list. That is how a real
gap stays a gap: the chart engine never draws a segment across a `null`
(`connectNulls: false`, web/static/web/js/charts.js).
"""

from __future__ import annotations

import datetime as dt

from django.db import connection
from django.utils import timezone

from . import charts

# A fixed origin keeps bucket edges identical between requests (and between
# gunicorn workers), so a cached response and a fresh one line up exactly.
BUCKET_ORIGIN = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)


def _number_sql(key_param: str) -> str:
    # The CASE guards the cast: a provider sending "" or "n/a" in a numeric
    # field turns that one value null instead of failing the whole query.
    return f"CASE WHEN jsonb_typeof(payload -> {key_param}) = 'number' THEN (payload -> {key_param})::float8 END"


def _aggregates_sql(column: str) -> str:
    # Fill values (and, on a log axis, values <= 0) are dropped by the
    # FILTER before any of the three aggregates sees them.
    keep = f"FILTER (WHERE {column} > %s)"
    return f"avg({column}) {keep}, min({column}) {keep}, max({column}) {keep}"


def _floor_for(series: charts.Series, log_axis: bool) -> float:
    # A log axis can't place 0 or a negative value, and on a flux a 0 means
    # "below detection", not a reading — drop it with the fill values.
    return 0.0 if log_axis else float(charts.FILL_VALUE_CEILING)


def _series_specs(product_id):
    specs = []
    for panel in charts.PRODUCT_PANELS.get(product_id, ()):
        for s in panel.series:
            specs.append((s, _floor_for(s, panel.y == "log")))
    return specs


def _window(range_key: str, now: dt.datetime) -> tuple[dt.datetime, int]:
    bucket = charts.BUCKET_SECONDS[range_key]
    since = now - dt.timedelta(days=charts.RANGE_DAYS[range_key])
    # Snap the start down to a bucket edge so the first bucket is complete.
    offset = (since - BUCKET_ORIGIN).total_seconds() % bucket
    return since - dt.timedelta(seconds=offset), bucket


# The JSONB values are extracted once per row inside a MATERIALIZED CTE.
# Without it PostgreSQL inlines the subquery and re-extracts every value
# for each of avg/min/max — measured 3x slower on a 7-day mag query.
_POINTS_CTE = (
    "WITH pts AS MATERIALIZED (SELECT extract(epoch FROM date_bin(%s, time_tag, %s))::bigint AS b, "
    "time_tag, source, {columns} FROM core_timeseries WHERE product_id = %s AND time_tag >= %s{extra}) "
)
_FLUX_SQL = _number_sql("'flux'")
_LAST_SOURCE_SQL = "(array_agg(source ORDER BY time_tag DESC) FILTER (WHERE source <> ''))[1]"


def _query_fields(product_id, specs, since, bucket):
    """Products with one column per field (mag, wind, Kp, Dst)."""
    columns, column_params, aggregates, aggregate_params = [], [], [], []
    for i, (s, floor) in enumerate(specs):
        columns.append(f"{_number_sql('%s')} AS v{i}")
        column_params += [s.field, s.field]
        aggregates.append(_aggregates_sql(f"v{i}"))
        aggregate_params += [floor] * 3
    sql = (
        _POINTS_CTE.format(columns=", ".join(columns), extra="")
        + "SELECT b, " + ", ".join(aggregates) + f", {_LAST_SOURCE_SQL} FROM pts GROUP BY b ORDER BY b"
    )
    params = [dt.timedelta(seconds=bucket), BUCKET_ORIGIN, *column_params, product_id, since, *aggregate_params]
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    out = []
    for row in rows:
        values = {}
        for i, (s, _floor) in enumerate(specs):
            values[s.field] = row[1 + 3 * i: 4 + 3 * i]
        out.append((row[0], values, row[-1]))
    return out


def _query_channels(product_id, specs, since, bucket):
    """Energy-channel products (X-ray, protons) store one row per channel per
    timestamp; the channel is `payload->>'energy'` and the value is always
    `flux`. Grouping by (bucket, channel) keeps channels aligned on the same
    bucket times by construction."""
    channels = [s.field for s, _floor in specs]
    floor = max(floor for _s, floor in specs)  # every channel of one panel shares one axis
    sql = (
        _POINTS_CTE.format(columns=f"payload ->> 'energy' AS ch, {_FLUX_SQL} AS v",
                           extra=" AND payload ->> 'energy' = ANY(%s)")
        + f"SELECT b, ch, {_aggregates_sql('v')}, {_LAST_SOURCE_SQL} FROM pts GROUP BY b, ch ORDER BY b"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, [dt.timedelta(seconds=bucket), BUCKET_ORIGIN, product_id, since, channels,
                             floor, floor, floor])
        rows = cursor.fetchall()
    values_by_bucket: dict[int, dict] = {}
    source_by_bucket: dict[int, str] = {}
    for b, ch, mean, lo, hi, source in rows:
        values_by_bucket.setdefault(b, {})[ch] = (mean, lo, hi)
        if source:
            source_by_bucket[b] = source
    return [(b, values_by_bucket[b], source_by_bucket.get(b)) for b in sorted(values_by_bucket)]


def _cadence_seconds(times: list[int], bucket: int) -> int:
    # The series' own cadence, not the manifest's polling `cadence` (D-054:
    # noaa.kp is polled every 15 min for data NOAA updates every 3 h). Kp in
    # 2-min buckets is still one point per 3 h, and that's not a gap.
    if len(times) < 3:
        return bucket
    deltas = sorted(b - a for a, b in zip(times, times[1:], strict=False))
    return max(bucket, deltas[len(deltas) // 2])


def bucketed_series(product_id: str, range_key: str, now: dt.datetime | None = None) -> dict:
    specs = _series_specs(product_id)
    since, bucket = _window(range_key, now or timezone.now())
    if product_id in charts.ENERGY_CHANNEL_PRODUCTS:
        buckets = _query_channels(product_id, specs, since, bucket)
    else:
        buckets = _query_fields(product_id, specs, since, bucket)

    step = _cadence_seconds([b for b, _v, _s in buckets], bucket)
    empty = {s.field: (None, None, None) for s, _floor in specs}
    filled: list[tuple[int, dict, str | None]] = []
    for b, values, source in buckets:
        if filled and b - filled[-1][0] > 1.5 * step:
            # Each missing step between two real buckets becomes an explicit
            # null bucket (D-056; replaces v0.1.2's _insert_gap_markers).
            # The 1.5x margin matters when the data is sparser than the
            # bucket (5-min protons in 2-min buckets land 4 or 6 min apart,
            # which is not a gap).
            t = filled[-1][0] + step
            while t <= b - step:
                filled.append((t, empty, None))
                t += step
        filled.append((b, values, source))

    payload = {
        "time": [b for b, _v, _s in filled],
        "series": {}, "min": {}, "max": {},
        "source": [source for _b, _v, source in filled],
        "bucket_seconds": bucket,
    }
    for s, _floor in specs:
        triples = [values.get(s.field, (None, None, None)) for _b, values, _s in filled]
        payload["series"][s.field] = [s.round(t[0]) for t in triples]
        payload["min"][s.field] = [s.round(t[1]) for t in triples]
        payload["max"][s.field] = [s.round(t[2]) for t in triples]
    return payload
