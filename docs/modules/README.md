# Adding a module

A module is a self-contained product family under `modules/<name>/`. It may
add manifests, collectors/services, templates for its own product cards, and
tests with recorded fixtures. It may **not** edit `core/`, another module,
`climaespacial/` (settings) or `deploy/` — that boundary is what lets several
contributors (and AI sessions) work in this repo at once without stepping on
each other (D-010).

## Start one

```
python manage.py startmodule <name>
```

This copies `modules/_template/` into `modules/<name>/` and fills in the
name. Read the generated `modules/<name>/README.md` — it's the checklist.

## Shape of a module

```
modules/<name>/
  manifests/*.yaml     one product manifest per product (docs/ARCHITECTURE.md §4.1)
  client.py            thin HTTP client for the external source, if any
  adapters.py           pure functions: raw payload -> rows for swxkit (no network I/O — this is what gets tested)
  collectors.py         COLLECTORS = {product_id: callable}; wires client -> adapters -> core.swxkit
  tests/
    fixtures/*.json      a real, recorded payload from the provider
    test_adapters.py      adapters tested against the fixture
```

`modules/swpc/` is a complete worked example (six NOAA SWPC products:
L1 solar wind/IMF, Kp, scales, GOES X-ray and proton flux). `modules/kyoto/`
is the worked example of a HAPI source with a fallback route and revisable
values (Dst, AE).

## The parts that matter

- **Talk to the store only through `core/swxkit.py`** — `publish_timeseries`,
  `publish_event`, `publish_forecast`, `heartbeat`, and `read_series` to
  read observations. Never read or write `Product`/`TimeSeriesPoint`/
  `Event`/`Forecast` directly. `modules/baselines/` is the worked example of
  a module that reads one product and publishes a forecast.
- **Separate "fetch" from "parse."** `client.py` does network I/O;
  `adapters.py` is pure (dict in, dict out) so a test can feed it a fixture
  without a live source. This is also what makes "degrade, don't break"
  possible: when the provider changes its JSON shape, exactly one adapter
  breaks and its schema test catches it.
- **Spacecraft/instrument/source names are data, never hard-coded**
  (docs/DECISIONS.md D-040) — NOAA has already changed the active L1
  monitor once. Read `source`/`active` fields from the payload.
- **`manage.py validate_manifests`** must pass — run in CI on every PR.
- **`manage.py collect <product_id>`** should run end to end against a local
  database once a collector is wired up; this is also what a systemd timer
  calls in production (`deploy/swx-collect@.service`).

## Revisable sources

Some providers publish an interval while it is still filling, or replace a
value later (WDC Kyoto: the current Dst hour grows, then realtime →
provisional → final). By default `publish_timeseries` stores a `time_tag`
once and never again (D-049), which would freeze such a series at its first,
partial value. Declare it in the manifest instead (D-080):

```yaml
revise: {window: 6h, version_field: version}
```

Re-fetch a window that overlaps what's stored (for example the last 8 h for
a 6 h window) and publish it every run: unchanged rows are no-ops, changed
ones are updated with `revised_at` set. Put the provider's processing level
in `version_field` when it has one, so an older level can never overwrite a
newer one. A one-off backfill that should also repair older rows passes
`revise_since=`.

## Delayed sources (`delayed_after`)

A collector can be perfectly healthy while its provider's data run behind
(WDC Kyoto's HAPI copy of AE stops weeks back, D-082). `stale_after` is about
*our* producer: keep it loose enough that such a product isn't red. Then
say how old the newest datum may get before the portal says so (D-085):

```yaml
stale_after: 30d
delayed_after: 2d
```

A product whose heartbeat is ok but whose newest stored datum is older than
`delayed_after` shows "al día · con retraso" / "up to date · delayed" (yellow)
on the catalogue, its page and `/estado/`, with "datos hasta dd/mm" as the
tooltip, and its headline values get their time ("al 04/10 23:55 UT").
`stale`, `error` and `no_data` always win over it. Optional; leave it out
for a live source.

## Exporting a product (`exchange.hapi`)

`exchange: {hapi: true}` puts a `timeseries`/`index` product on the portal's
HAPI server (`/hapi/`) and behind the CSV button on its page
(`/api/v1/datos/<id>.csv`), D-087/D-088. Nothing else is needed: columns,
units, cadence and dates come from the manifest and the stored data. A
product with several rows per `time_tag` (an `energy` channel) is pivoted to
one column per channel.

- Set it only if `source.license` lets anyone redistribute the data
  (public domain, CC BY 4.0, CC0). `validate_manifests` refuses
  `hapi: true` with any other licence, so Kyoto's non-commercial Dst/AE stay
  out (D-078).
- A third party's data are exported as a *relay* (provider not USACH): the
  HAPI `info`, the CSV header and `/datos-api/` say so and point to the
  provider's own page (`source.links[0]`). Relaying is transitional (D-084):
  long term the portal exports what USACH produces.
- To withdraw a dataset, set `hapi: false`. It disappears from the HAPI
  catalog, `info`, `data` and downloads on the next request; no code change.
- `kind: forecast` products are exported separately, in the exchange format
  (`/api/v1/pronosticos/<id>.json|.csv`, D-089), whatever `exchange` says.

## Recording a fixture

Fetch a real response from the provider once, trim it to a reasonable size
(tens of records, not the full rolling window) while keeping whatever
variety the adapter needs to be tested honestly (e.g. both `active: true`
and `active: false` rows, every enum value that appears), and save it under
`tests/fixtures/`. Never commit personal data or an internal/synthetic
payload dressed up as real — a fixture is only useful if it's what the
provider actually sends.
