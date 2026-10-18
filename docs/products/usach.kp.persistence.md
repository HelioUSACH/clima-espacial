# Model card: `usach.kp.persistence`

## Summary

The reference forecast for Kp: the Kp of the next 3, 6, 12 and 24 hours
equals the last complete Kp interval. It is not a physical model. It is the
benchmark every Kp model on the portal is scored against, because a forecast
that cannot beat persistence adds no information. Its skill against
persistence is 0 by construction. Maturity: `experimental`. It is listed in
the catalogue and never shown on the home page.

## Inputs

`noaa.kp`: NOAA SWPC's near-real-time planetary Kp, 3-hourly, as stored by
this portal (`swxkit.read_series`). The provenance of every issue records the
newest Kp interval it used.

## Method

One issue per hour, `issued_at` on the hour (`swxkit.floor_issue_time`). The
run itself is at :35, because a Kp interval reaches the store 5–16 min after
it closes; a run at :05 (v0.4.0) missed the interval that had just closed at
00, 03, …, 21 UT (D-079). The product page shows "datos hasta / data up to"
next to the issue time, from the issue's provenance.

- **Value**: the persistence reference in `core.forecasting.persistence`,
  the last Kp interval complete at `issued_at` (its `time_tag` + 3 h ≤
  `issued_at`). An issue at 19:00 uses the 15:00–18:00 interval.
- **Band** (nominal 80 %): the value plus the 10th and 90th percentiles of
  persistence's own errors, Kp(t + L) − Kp(t), at that lead L, over hourly
  issue times in the last 30 days. Only intervals already complete at
  `issued_at` are used, so there is no look-ahead. The band is widened if
  needed to contain the value, then clipped to [0, 9]. With fewer than 20
  errors at a lead, the band is the whole range [0, 9].
- **Stale input**: if the newest Kp is more than 9 h older than `issued_at`,
  nothing is published and the heartbeat turns `error` ("target stale").
  A dead input is never persisted as a live forecast.

Code: `modules/baselines/persistence.py` (pure), `collectors.py` (I/O).
Model version `0.1.0`.

## Training data

None. The band's error distribution is recomputed at every issue from the
previous 30 days.

## Performance

Verified daily by `manage.py verify_forecasts` (03:15 UT) against `noaa.kp`
as first stored, over 7, 30 and 90 days of valid times. The product page's
"Desempeño / Performance" table shows n, MAE, RMSE, bias, skill and coverage
next to its nominal 80 %. Skill is 0 for this product by definition. Its use
is as the denominator of every other model's skill, and that skill is
computed from the observations, not from this product being up.

The reference is rebuilt from what was in the store when each forecast was
published (D-079), so this product's issued values should equal it. The
manifest marks it `baseline: persistence`, and `verify_forecasts` logs a
warning when more than 1 % of the last week's values differ, which means
the issue timing broke (NOAA late, the `noaa.kp` collector down).

## Known limitations

- Verified against NOAA's near-real-time Kp **as first stored**. The store is
  append-only (D-049), so a later revision of a Kp value by NOAA or GFZ is
  never seen, neither by the forecast nor by its verification.
- The band is the empirical spread of recent persistence errors. During a
  quiet month it is narrow, and it under-covers at the onset of a storm.
- Kp's 3-hour intervals mean that issues within the same interval share the
  same value. Hourly issues add timing resolution to the verification, not
  information.

## Maintainers / links

HelioUSACH, `HelioUSACH/clima-espacial` (this repository): code in
`modules/baselines/`, contract in `docs/models/INTEGRATION.md`.
