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

## Recording a fixture

Fetch a real response from the provider once, trim it to a reasonable size
(tens of records, not the full rolling window) while keeping whatever
variety the adapter needs to be tested honestly (e.g. both `active: true`
and `active: false` rows, every enum value that appears), and save it under
`tests/fixtures/`. Never commit personal data or an internal/synthetic
payload dressed up as real — a fixture is only useful if it's what the
provider actually sends.
