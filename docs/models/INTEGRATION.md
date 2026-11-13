# Integrating a model

> **Building a model in its own repository?** Start with
> [`CONTRIBUTING-A-MODEL.md`](CONTRIBUTING-A-MODEL.md): the checklist your
> repo (and its coding agent) runs before every push, and the integration
> sheet ([`INTEGRATION-SHEET-TEMPLATE.md`](INTEGRATION-SHEET-TEMPLATE.md))
> the maintainers review first (D-095). This file is the portal side.

Models are built and owned in **their own repository** (D-044) — weights
ship as a release asset there, never committed here, and never developed in
an OneDrive-synced folder (a shared folder written by two hosts at once has
already corrupted a model's cache once; see that repo's own audit notes).
This repo never imports a model's code. Integrating one is exactly the
product contract everything else in this repo already follows:

1. **A manifest** under `modules/<name>/manifests/` (docs/ARCHITECTURE.md
   §4.1) — `kind: event` for a detector/classifier, `kind: forecast` for a
   model that predicts an observed quantity (see "Forecast models" below), `maturity: experimental`
   or `beta` until there's a model card with real skill numbers (never
   `operational` for a model still being validated).
2. **A producer** that publishes through `core/swxkit.py`. Two shapes, pick
   whichever matches how the model actually runs:
   - **Same venv, in-process**: the model's own training/inference repo
     exposes a Django management-command-friendly entry point, or this repo
     adds a small collector that imports it. Rare — most models are a
     separate service (below).
   - **Separate service, polled** (the common case — `modules/flare/` is the
     worked example): the model runs as its own long-running service (here,
     a FastAPI process) exposing its own status/results endpoint. A small
     module in *this* repo (`client.py` + `adapters.py` + `collectors.py`,
     same shape as every other adapter — docs/modules/README.md) polls that
     endpoint on a systemd timer and republishes the result as an event via
     `swxkit.publish_event`. This repo never depends on the model repo's
     code, only on its HTTP contract.

   A third shape — the model repo pushing to an HTTP ingest endpoint on this
   side instead of being polled — is in the design (docs/ARCHITECTURE.md
   §4.2) but not built until a producer actually needs it (M2, D-048).
3. **A model card** (`MODEL-CARD-TEMPLATE.md` in this directory) at the path
   the manifest's `model_card` field points to — required before the
   product's maturity can move past `experimental`.
4. **Freshness and honesty.** Set `stale_after` to something a bit looser
   than the producer's own staleness threshold if it publishes one (so this
   repo's heartbeat tracks, not fights, the producer's own notion of
   "stale") — `modules/flare/manifests/nrt.yaml` picks `10m` to match
   flare-nrt's own `FLARE_NRT_STALE_AFTER_SEC` (600 s). Never republish a field the model
   itself flags as exploratory/low-confidence as if it were a validated
   signal (01-AUDIT.md §3.6 — `modules/flare/adapters.py` deliberately drops
   `severity` for exactly this reason). A product whose producer isn't
   running yet (or isn't stable) must degrade to "en preparación"/stale
   automatically, from its real heartbeat — never a hard-coded flag in the
   view (see `web/views.py::_flare_card_ready`, D-036).

## Worked example: `modules/flare/` (flare-nrt 1.x)

The producer is [`HelioUSACH/flare-nrt`](https://github.com/HelioUSACH/flare-nrt)
(v1.2.0 at the time of writing). Its integration sheet (`INTEGRATION.md`
there, sections "GET /api/status", "Display endpoints" and "Display files
(1.1.0, 1.2.0)") is the contract; this repo uses only the fields and endpoints it
lists.

**The collector** polls the internal `/api/status` (never exposed by nginx,
docs/ARCHITECTURE.md §6) every minute and republishes the result as a
`usach.flare.nrt` event (manifest version 2, D-098):

| event field | from `/api/status` |
|---|---|
| `issued` | `timestamp`: when the result was made, unique per result; the de-duplication key (D-096) |
| `valid_from` | `observation_time`: the AIA slot the result is about (falls back to `timestamp` for a producer before 1.0) |
| `payload` | `probability`, `threshold`, `alert`, `state` (`quiet`/`onset`/`active`/`decay`), `flare_detected`, `event_id`, `event_t_start`, `location` (experimental; its documented keys only) |
| `quality` | `live_status`, `input_age_s`, `ensemble_std` |
| `provenance` | `{"producer": "flare-nrt@<model_version>", "inputs": {"131": T_OBS, "171": …, "304": …, "1600": …}}` |

Events stored before version 2 (only `probability`, `flare_detected`,
`location`, with `valid_from = issued`) stay as they are and still render.
Service-health fields (`ensemble_scores`, latency, loop counters) are not
republished; neither was `severity` while the producer sent it (01-AUDIT.md
§3.6).

The producer's own `live_status` decides (D-096): only `live` is published,
and a result already stored (same `issued`; the producer updates every
2 min) is not stored again; `stale` turns the heartbeat `stale` with the
producer's `seconds_since_update` in its detail; `no_data`, `unknown` or a
missing value is `no_data`. A naive `timestamp` is read as UTC. If the
service isn't reachable, the heartbeat is `error`, which the public sees as
"en preparación" (D-036).

**The product page** (D-099..D-104, D-106..D-112) also shows what only the service has:
the probability per slot, the alert history, the images, the last hours as
a loop and each event's replay. Django reads `/api/series`, `/api/events`,
`/api/loop` and `/api/events/{id}/replay` server-side (whitelisted, 2 s
timeout, 30 s cache, `web/flare.py`) and hands the browser its own
`datos/*.json` with every image URL rewritten to a public nginx path. The
images reach the browser through cached nginx locations, never through
Django (docs/ARCHITECTURE.md §6.1):

| public path (under `/clima-espacial/productos/usach.flare.nrt/`) | flare-nrt 1.1.0 / 1.2.0 |
|---|---|
| `img/{wave}.png?kind=img\|diff&slot=ISO&format=webp&size=1024` | `/api/image/{wave}.png` (1024 px with `kind=img` only) |
| `gradcam/{latest\|ISO}.png?style=heat&format=webp` | `/api/gradcam/…` (the map alone, inferno, normalised to its own maximum) |
| `render/{ISO}/{171\|171-diff\|gradcam-heat}.webp` | `/api/render/…`, the frames `/api/loop` lists |
| `replay/{event_id}/{ISO}/{131\|171\|gradcam-heat}.webp` | `/api/events/{id}/frames/…`, the frames of `replay.json` (`131` since 1.2.0: an old event's peak image once the FITS are gone, D-107) |
| `replay/{event_id}/peak/{131\|171\|304\|1600\|gradcam-heat\|gradcam-overlay}.webp` | `/api/events/{id}/peak/…` (1.2.0), the event's peak set; the page asks only for the names `replay.json`'s `peak.images` lists (D-112) |

The page uses the heat style only for an alert frame (D-102): normalised
to its own maximum, a quiet frame's map looks as strong as a flare's. An
event's map is taken, in order, from its peak set's `gradcam-heat`, the
replay's heat frame at the peak slot, the live `gradcam/<slot>?style=heat`,
then the overlay (labelled as such); its 131 Å from the peak set's `131`,
the live image, the replay's 131 Å frame, then none (D-112). A map made by
flare-nrt 1.0.0 has no heat style, so for such an event only the peak set
(1.2.0) has one. A
model whose page needs images or other display data can follow the same
split: the product store for what the portal publishes, server-side JSON
for the rest, nginx for anything binary.

## Forecast models

A model that predicts an observed quantity (Kp, Hp30, Dst, dB/dt at a
station) publishes as `kind: forecast`, so every issue can be verified
against what was then observed and combined with other models and
institutions. The decisions are D-071 to D-076.

### Manifest

```yaml
id: usach.kp.persistence          # the model id IS the product id; the namespace carries the institution
kind: forecast
cadence: 1h                       # the ISSUE cadence
model_card: docs/products/usach.kp.persistence.md   # required for kind: forecast
producer: {type: collector, unit: "swx-collect@usach.kp.persistence.timer", repo: "HelioUSACH/clima-espacial"}
forecast:
  target: {product: noaa.kp, field: kp}   # an observed product (timeseries/index) + one of its parameters
  horizons: [3h, 6h, 12h, 24h]
  uncertainty: {type: bounds, level: 0.8} # none | bounds | quantiles (reserved, rejected in 0.4)
  sites: false                            # true: every row carries a site code (station, grid cell)
```

The model *version* is not in the manifest. The running code knows it and
passes it with every issue. The manifest `version` keeps its usual meaning
(product/schema version). The target's native step (Kp: 3 h) is read from
the target's data, not declared.

### Publishing an issue

```python
from core import swxkit

issued_at = swxkit.floor_issue_time("usach.kp.persistence")   # now, floored to the manifest cadence
swxkit.publish_forecast(
    "usach.kp.persistence",
    issued_at=issued_at,
    model_version="0.1.0",
    rows=[
        {"valid_at": issued_at + dt.timedelta(hours=3), "kp": {"value": 2.33, "lower": 1.33, "upper": 3.67}},
        {"valid_at": issued_at + dt.timedelta(hours=6), "kp": {"value": 2.33, "lower": 1.0, "upper": 4.0}},
        # ... one row per horizon (and per site, with sites: true: "site": "SCL")
    ],
    provenance={"inputs": [{"product": "noaa.kp", "latest": "2026-10-17T15:00:00Z"}],
                "producer": "baselines.kp_persistence@0.1.0"},
)
```

- **`issued_at` is deterministic.** Floor it to the manifest `cadence`
  (`floor_issue_time` does this). An `issued_at` off the cadence grid is
  rejected, because a re-run a few seconds later must collide with the
  first run, not add a near-duplicate issue.
- **An issue is write-once per `model_version`.** If any row of (product,
  `issued_at`, `model_version`) is already stored, nothing is inserted and
  0 is returned. A *different* issue under the same version is ignored and
  logged as a warning: a correction ships as a new `model_version`.
- **Rejected** (nothing written, `PublishError`):
  - a lead that isn't a declared horizon to within ±60 s;
  - a field other than `target.field`;
  - `lower > value` or `value > upper`;
  - bounds missing under `bounds`, or present under `none`;
  - a non-empty `site` with `sites: false`, or an empty one with `sites: true`;
  - naive datetimes;
  - `uncertainty: quantiles`;
  - a repeated (`valid_at`, `site`) within one issue.
- **Heartbeat**: `ok` on publish (a no-op re-run included), `no_data` on
  zero rows. A producer whose input is stale sets `error` itself and
  publishes nothing.
- Read observations with `swxkit.read_series(product, field, since)` (with
  `site=` for a site-dimensioned target, whose rows carry `site` in their
  payload), never by querying `core_timeseries`.

Rows are stored in `core_forecast` (one row per issue × valid time × site,
monthly partitions on `issued_at`, D-072), as
`values = {field: {"value", "lower", "upper"}}`, the exchange format's own
keys.

### Worked example: `modules/baselines/` (`usach.kp.persistence`)

The persistence baseline is a complete producer in two files. It is the
benchmark every Kp model must beat.

- `persistence.py` is pure: observations + `issued_at` → rows. Value = the
  last complete Kp interval. Band = value + the 10th/90th percentiles of
  persistence's own errors at that lead over the last 30 days, with no
  look-ahead. A stale target raises `TargetStale`.
- `collectors.py` does the I/O: `read_series("noaa.kp", "kp", …)`, then
  `publish_forecast(...)`, or `heartbeat(..., "error")` if the target is
  stale. `COLLECTORS` maps the product id to it, and
  `deploy/systemd/swx-collect@usach.kp.persistence.timer` runs it hourly at
  :35. A Kp interval reaches the store 5–16 min after it closes, so a run
  just after the hour would miss the interval that had just closed (D-079).
  Time your own runs after your inputs arrive, not after the boundary.

A model in its own repository has the same shape. Its service exposes its
latest issue over HTTP, and a small module here polls it and calls
`publish_forecast` (see `modules/flare/` for the polling half).

### Verification

`manage.py verify_forecasts` (daily, 03:15 UT, `swx-verify.timer`) scores
each (product, model_version, lead, site) over 7, 30 and 90 days of valid
times, into `ForecastScore`. The rules live in `core/forecasting.py`, one
function each:

- **Matching rule** (D-074): a forecast valid at `valid_at` is verified by
  the target sample whose interval contains it, i.e. `time_tag` in
  `(valid_at − step, valid_at]`, with `time_tag` at interval start. A Kp
  forecast valid at 17:00 is verified by the 15:00–18:00 interval; for a
  minute-cadence target, the sample at or just before. Forecasts with no
  matching observation are skipped and counted (`n_unverified`). Forecasts
  whose verifying interval hasn't closed yet are not counted at all.
- **Persistence reference** (D-074, D-079): the last observation complete
  at `issued_at` *that was already in the store when the forecast was
  published* (observation `inserted_at` ≤ forecast `inserted_at`). That is
  the persistence forecast anyone could have issued at that moment, so the
  comparison is fair whatever your model's latency. It is computed from the
  observations, so no model's score depends on the baseline product being
  up. For replayed or backfilled data, where no observation passes that
  test, the reference falls back to `time_tag` alone and those pairs are
  counted in `n_reference_fallback`.
- **Metrics** (D-075): n, MAE, RMSE and bias (forecast − observed); skill =
  1 − MSE / MSE_persistence, null when n < 20 or MSE_persistence = 0; and,
  with bounds, coverage next to its nominal level.

Observations are the target **as first stored**: the store is append-only
(D-049), so a provider's later revision is never seen. Say so in the model
card.

## Forecast exchange format

The format for exchanging forecasts with other nodes of the national network
(D-076). It is one record per (issue, valid time, site), proposed to
UnACh/UChile as the network standard: don't rename a field without updating
that proposal.

| field | type | meaning |
|---|---|---|
| `model_id` | string | the product id, e.g. `usach.kp.persistence` |
| `model_version` | string | the producing code's version, e.g. `0.1.0` |
| `issued_at` | time | when the forecast was issued (floored to the issue cadence) |
| `valid_at` | time | the instant it is valid for |
| `lead_seconds` | integer | `valid_at − issued_at` |
| `site` | string | station/cell code; `""` for a global index |
| `variable` | string | the forecast quantity, e.g. `kp` |
| `units` | string | its units (`"1"` for a dimensionless index) |
| `value` | number | the forecast |
| `lower`, `upper` | number or null | the central interval's bounds; null without uncertainty |
| `interval_level` | number or null | its nominal coverage, e.g. `0.8`; null without uncertainty |
| `provenance` | object | `{"inputs": [{"product", "latest"}], "producer": "<name>@<version>"}` |

Times are ISO 8601 UTC with `Z`, to the second (`2026-10-17T19:00:00Z`).

JSON (an array of records):

```json
[
  {"model_id": "usach.kp.persistence", "model_version": "0.1.0",
   "issued_at": "2026-10-17T19:00:00Z", "valid_at": "2026-10-17T22:00:00Z", "lead_seconds": 10800,
   "site": "", "variable": "kp", "units": "1",
   "value": 2.33, "lower": 1.33, "upper": 3.67, "interval_level": 0.8,
   "provenance": {"inputs": [{"product": "noaa.kp", "latest": "2026-10-17T15:00:00Z"}],
                  "producer": "baselines.kp_persistence@0.1.0"}},
  {"model_id": "example.dst.model", "model_version": "2.1", "issued_at": "2026-10-17T19:00:00Z",
   "valid_at": "2026-10-17T20:00:00Z", "lead_seconds": 3600, "site": "", "variable": "dst", "units": "nT",
   "value": -23.0, "lower": null, "upper": null, "interval_level": null,
   "provenance": {"inputs": [{"product": "noaa.rtsw.wind", "latest": "2026-10-17T18:59:00Z"}], "producer": "example-dst@2.1"}}
]
```

CSV (header row; `provenance` as compact JSON in one quoted field; null as
an empty field):

```csv
model_id,model_version,issued_at,valid_at,lead_seconds,site,variable,units,value,lower,upper,interval_level,provenance
usach.kp.persistence,0.1.0,2026-10-17T19:00:00Z,2026-10-17T22:00:00Z,10800,,kp,1,2.33,1.33,3.67,0.8,"{""inputs"":[{""product"":""noaa.kp"",""latest"":""2026-10-17T15:00:00Z""}],""producer"":""baselines.kp_persistence@0.1.0""}"
usach.kp.persistence,0.1.0,2026-10-17T19:00:00Z,2026-10-18T19:00:00Z,86400,,kp,1,2.33,0.0,5.67,0.8,"{""inputs"":[{""product"":""noaa.kp"",""latest"":""2026-10-17T15:00:00Z""}],""producer"":""baselines.kp_persistence@0.1.0""}"
```

The second JSON record is a format illustration only (a hypothetical
external model), not a real product.

On this portal, `productos/<id>/forecast.json` serves an issue (`?issued=latest|<iso>&range=1d|3d|7d`) or
a lead's verification pairs (`?lead=3h&range=7d|30d`) for the chart. That is a
chart-oriented columnar shape (`web/forecasts.py`), not the exchange format.


The exchange format itself is served at `/api/v1/pronosticos/<id>.json`
and `.csv` (D-089): the latest issue with no parameters (no API key
needed), or `?issued_since=…&issued_until=…` (up to 31 days, with a key).
A new forecast product appears there as soon as it publishes; nothing to
add.
