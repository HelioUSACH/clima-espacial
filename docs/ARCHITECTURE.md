# Architecture

Status: living document. Decided items cite a `D-xxx` entry in
`DECISIONS.md`; a later entry supersedes an earlier one on conflict.

## 1. Mission

A USACH-operated web portal and data service that (1) shows the
space-weather conditions that matter for Chile, with emphasis on what
HelioUSACH uniquely produces (an NRT flare detector, and future models), (2)
acts as the **USACH node of the Plataforma Nacional de Meteorología
Espacial**, delivering products to it through a versioned interface, and
(3) grows by **adding product modules without touching the core** (D-001).

## 2. Design principles

1. **Everything is a product.** A solar-wind series, a flare detection, a
   geomagnetic index: each is a *product* with an ID, a version, a schema,
   provenance and a status. UI, public API and outbound exchange all read the
   same product store, so a new product shows up everywhere at once.
2. **Producers are plugins.** Collectors (scheduled) and services
   (long-running models) are independent processes that publish products
   through one small contract (the `swxkit` SDK, or HTTP for producers off
   the VM).
3. **Adapters at every border.** External sources in, national platform out.
   A provider change (NOAA SWPC has already changed its JSON shape once,
   D-040) or protocol change stays inside one adapter.
4. **Degrade, don't break.** Each product has a freshness rule
   (`stale_after`); stale/failed products are shown as such; the rest of the
   portal keeps working.
5. **Boring, shared stack** (D-002): Django LTS, PostgreSQL, systemd, nginx,
   no JS build — the same conventions as the Department's platform, so
   contributors learn one way of doing things.
6. **Honest science.** Research products are labelled by maturity
   (experimental/beta/operational), with a model card; nothing is presented
   as an official warning.
7. **Bilingual from day one** (ES primary, EN secondary, D-008).
8. **Contributors are humans *and* AI sessions** (D-010): conventions are
   written down (`CLAUDE.md`/`AGENTS.md`), modules follow a template, and CI
   — not goodwill — enforces the contract.

## 3. System view

```
 SOURCES                      PRODUCERS (plugins)                      CORE (helio VM)                       CONSUMERS
 ───────                      ──────────────────                       ───────────────                       ─────────
 NOAA SWPC JSON ─┐            collector: swpc.*     (timer, 1 min) ─┐
 Kyoto WDC / GFZ ├ adapters ► collector: indices     (timer)        │   ┌───────────────────────────┐
 JSOC / SDO      │            service:   flare-nrt   (loop, 3 min) ─┼─► │ PRODUCT STORE             │ ─► Web portal (public, ES/EN)
 future sources  ┘            future collectors/services             │   PostgreSQL 16              │ ─► HAPI + CSV + forecast export (v0.5.0)
                                                                      │   • product registry          │ ─► JSON feeds (events, status)
                                                                      │   • time series (partitioned) │ ─► Exchange adapters ─► Plataforma Nacional
                                                                      │   • events / bulletins        │                       (pull now, push later)
                                                                      │   files: /srv/swx/data        │ ─► Alerts (dormant, D-020)
                                                                      └──────────────┬────────────────┘ ─► HelioUSACH site (link)
                                                                                     │ nightly pg_dump/rsync
                                                                                     ▼
                                                                      Backups pulled by an off-machine host (D-039)
```

## 4. The product contract

### 4.1 Product manifest (one YAML per product, versioned in git)

```yaml
id: usach.flare.nrt            # namespace.domain.name
version: 3                     # bump on schema change; old versions stay readable
kind: event                    # timeseries | event | image | bulletin | index | status | forecast
title: {es: "Detección de fulguraciones (NRT)", en: "Flare detection (NRT)"}
maturity: beta                 # experimental | beta | operational
cadence: 180s
stale_after: 15m
delayed_after: 2d              # optional, D-085: healthy but data older than this -> "al día · con retraso"
producer: {type: service, unit: swx-flare-loop, repo: HelioUSACH/flare-nrt}
source:
  provider: "HelioUSACH (USACH)"
  inputs: ["SDO/AIA synoptic via JSOC"]
  license: "CC-BY-4.0"
  links: []                    # optional, D-078: [{name, url}] -- the provider's own page(s)
                               # for this product, primary first; https only, name not translated
parameters:
  - {name: probability, units: "1", range: [0, 1]}
  - {name: state, enum: [QUIET, RISING, POST_IMPULSIVE]}
exchange: {hapi: true, feed: true, push: [national-platform]}   # what may leave the portal (hapi: D-087)
revise: {window: 6h, version_field: version}   # optional, D-080: a revisable series (below)
model_card: docs/products/usach.flare.nrt.md
description: {es: "...", en: "..."}   # optional: the product page's "Qué muestra" text
facts:                                # optional: the product page's "Ficha del producto" <dl>
  source: {es: "...", en: "..."}      # recognised keys: source, spacecraft, cadence,
  license: {es: "...", en: "..."}     # coordinates, license, citation, used_by -- a product skips
                                       # any row it doesn't set, never shows one empty
```

`revise` (D-080) is for a provider that publishes an interval while it is
still filling, or revises values later (Kyoto's Dst: the current hour grows,
then realtime → provisional → final). Without it, `publish_timeseries`
skips every row at or before the latest stored `time_tag` (D-049), so such a
series would stay frozen at its first, partial value. With it, a row with
`time_tag >= latest − window` that differs from the stored one is updated in
place (payload, `source`, `revised_at`), and a hole inside the window is
filled. With `version_field`, a lower incoming version never overwrites a
higher one. Row identity is `(product, time_tag)`, so `revise` is refused
on a product with several rows per `time_tag` (the `energy` channels, D-056);
`revise.key` is reserved for that and not supported yet.

`source.links` (D-078) turns the provider into a link on the product page
(the provider pill links to the first entry; "Ficha del producto" lists them
all). `validate_manifests` warns, without failing, when a product whose
provider isn't HelioUSACH has none. Third-party data keep their provider's
terms: `source.license` says what they are, and `exchange` stays off for data
the provider doesn't let us redistribute (Kyoto's real-time Dst).

A `kind: forecast` product (D-071) also carries a `forecast` block, and
`model_card` becomes required. `cadence` is the issue cadence, the model id
is the product id, and the model version is passed per issue (§4.2):

```yaml
forecast:
  target: {product: noaa.kp, field: kp}   # an observed product (timeseries/index) + one of its parameters
  horizons: [3h, 6h, 12h, 24h]
  uncertainty: {type: bounds, level: 0.8} # none | bounds (central interval, nominal coverage); quantiles reserved
  sites: false                            # true if rows carry a site code (stations, grid cells)
  baseline: persistence                   # optional (D-079): this product IS the persistence reference
```

The block is forbidden on every other kind. `validate_manifests` also checks
across manifests that the target exists, is observed, and has that field.

Validated against `core/manifests/schema.json` by `manage.py
validate_manifests` (also run in CI). See `docs/modules/README.md` for how a
module ships its manifests.

### 4.2 Publishing API for producers (`core/swxkit.py`)

- `publish_timeseries(product, rows, *, quality=None, provenance=None, revise_since=None)` —
  `rows` is an iterable of plain dicts, one key per manifest parameter plus
  `time_tag`; no pandas dependency. Skip-if-not-newer (D-049), or, for a
  `revise` product, update-in-window in one transaction (D-080).
  `revise_since` widens the window for a one-off backfill/repair command.
- `publish_event(product, payload, *, valid_from, valid_to=None, quality=None, provenance=None)`.
- `publish_forecast(product, *, issued_at, model_version, rows, provenance)`
  — one issue of a `kind: forecast` product into `core_forecast` (D-072,
  D-073). `issued_at` is floored to the manifest cadence
  (`floor_issue_time`), so an issue is deterministic and write-once per
  model version; a re-run inserts nothing. Contract and worked example in
  `docs/models/INTEGRATION.md`.
- `heartbeat(product, status, *, detail=None)` — `status` is one of `ok`,
  `stale`, `error`, `no_data`; fed into `/estado/`.
- `read_series(product, field, since, until=None, *, site="", with_inserted=False)`
  — the read side: `[(time_tag, value)]` of one numeric field (with
  `inserted_at` as a third item if asked). A module reads observations
  through this, never by querying `core_timeseries`.

Forecasts are verified daily by `manage.py verify_forecasts`
(`swx-verify.timer`, D-075), against the matching rule and persistence
reference in `core/forecasting.py` (D-074). The reference is what was in the
store when the forecast was published (`inserted_at`, D-079).

All three resolve the product's current manifest version, validate the
payload shape against it, and raise instead of silently writing malformed
data. HTTP ingest (same envelope, token-authenticated) is deferred to M2 for
producers that can't import the SDK directly (D-023 keeps the design room,
doesn't build it yet).

### 4.3 Canonical message envelope (internal; exchange adapters translate it)

```json
{
  "schema": "swx-usach/product-message/1",
  "message_id": "b3f1…",
  "product": "usach.flare.nrt", "product_version": 3,
  "type": "event",
  "issued": "2026-10-07T14:03:00Z",
  "valid": {"from": "2026-10-07T14:00:00Z", "to": null},
  "payload": {"probability": 0.91, "state": "RISING"},
  "quality": {"flags": [], "maturity": "beta"},
  "provenance": {"producer": "flare-nrt@v3.0.2", "inputs": ["aia.synoptic 2026-10-07T13:59:56Z"]},
  "links": {"self": "https://…/api/v1/messages/b3f1…", "product": "https://…/productos/usach.flare.nrt"}
}
```

`type` is the product's `kind` (`event`, `timeseries`, `forecast`, …). A
forecast's payload is one record of the forecast exchange format
(`docs/models/INTEGRATION.md`, D-076).

### 4.4 Outbound exchange (D-013)

**Built (v0.5.0):** pull access, in the `api/` app. Everything is
language-neutral and lives outside `i18n_patterns`.

1. **One export layer** (`api/export.py`, D-087) reads what is exportable
   from the manifests: a `timeseries`/`index` product with
   `exchange.hapi: true` whose `source.license` allows redistribution
   (D-078; `validate_manifests` refuses anything else, so Kyoto stays out).
   Rows are the raw stored samples, ordered by `time_tag`, streamed from a
   server-side cursor; a long-format product (GOES `energy` channels) is
   pivoted to one column per channel plus the satellite. Missing and
   provider-fill values become `-1.0E31`. Per request: at most 31 days for a
   series at ≤ 5 min (measured from the data, not the manifest), 366 days
   otherwise, and 200 000 rows.
2. **HAPI 3.3 server** at `/hapi/` (`api/hapi.py`, D-088): about,
   capabilities, catalog, info, data, CSV and JSON, HAPI 2 parameter names
   accepted, CORS open, HAPI status JSON on every error. Checked with the
   community verifier (`deploy/hapi-verify.sh`). `/hapi` itself redirects
   to the `#hapi` section of `/datos-api/` in the visitor's language
   (D-093), so the human-facing page is part of the bilingual site.
3. **CSV downloads** at `/api/v1/datos/<id>.csv?start=…&stop=…`, the same
   rows with a `#` header block (title, page, provider, licence, citation,
   retrieval time, units); the product page's CSV button opens a dialog.
4. **Forecast export** at `/api/v1/pronosticos/<id>.json|.csv` in the
   exchange format (D-076, D-089): the latest issue, or up to 31 days of
   issues.
5. **Access** (`api/access.py`, D-083, D-086): `data`, downloads and the
   forecast range need a free API key (`X-API-Key` header or `api_key`
   parameter), issued at registration on `/datos-api/` and shown once; only
   its SHA-256 is stored. Without a key, a span ≤ 24 h ending in the last
   7 days (or the latest forecast issue) is allowed, 30/h per IP. Per key:
   60/min, 5 000 requests and 2 000 000 rows per UTC day, service keys 10×.
   Usage is one row per (key, day, endpoint) in `ApiUsageDay`;
   `manage.py api_usage_report` turns it into figures by sector.

Each exported dataset is `own` (provider USACH) or `relay` (a third
party's). Relaying is transitional (D-084): relay datasets say so and link
the official provider, and withdrawing one is setting `exchange.hapi: false`.

**Not built yet:** the JSON event feed, and the **push adapter** (HTTPS POST
of the envelope, HMAC-signed, per-subscriber product filter), or whatever
the national platform's own ingest API specifies (D-041), added as a new
adapter, core untouched.

## 5. Stack (D-002)

Django 5.2 LTS + PostgreSQL 16 + HTMX, server-rendered templates, i18n
built in. Chart library: vendored **uPlot** (MIT, D-042) — no CDN, no JS
build. The flare model stays an independent FastAPI producer service behind
nginx; the portal only ever consumes its JSON.

Storage: plain PostgreSQL 16 with declarative monthly partitioning and BRIN
indexes on the time-series table (D-032); no TimescaleDB for now.

## 6. Deployment on the helio VM — systemd, no Docker (D-012); path `/clima-espacial/` (D-005)

```
nginx
 ├─ helio.fisica.usach.cl          → /var/www/helio (group site, unchanged)
 └─ /clima-espacial/               → gunicorn (FORCE_SCRIPT_NAME=/clima-espacial) 127.0.0.1:8100
      ├─ /clima-espacial/static/    → hashed static files served by nginx, cached immutable (D-055)
      ├─ /clima-espacial/…/serie.json → gunicorn; bucketed in SQL, cached 60 s (D-056/D-057)
      └─ (internal) flare FastAPI   127.0.0.1:5050, not exposed; portal reads its JSON

/opt/swx/            code (root-owned, read-only to the service user)
/srv/swx/data/       rolling product files (images, downloads) with retention
/etc/swx/swx.env     secrets
systemd: swx-web, swx-collect@<product>.timer, swx-dump.timer, swx-verify.timer
          (+ the flare repo's own units, started independently)
```

Indicative RAM budget (15 GiB): PostgreSQL 2–3 GB · gunicorn 2 gthread workers × 4 threads (D-069)
· flare loop (CPU) 2–3 GB · collectors <0.5 GB · OS/cache rest. Disk (96 GB
–4 GB swap): keep a rolling window of heavy files on the VM; long-term
archive is a separate concern (D-015/D-038), out of scope for M1.

## 7. Information architecture (public portal, v1 — stakeholders first, D-034/D-037)

```
Inicio — franja de contexto en vivo (escalas G/S/R, viento solar L1 + nave fuente, Kp, Dst, rayos X, protones)
         + oferta USACH al frente: fulguraciones NRT (beta), modelos y productos, "qué ofrecemos"
Productos y modelos — catálogo con madurez (beta/operacional), hoja de ruta
Qué ofrecemos — para operadores eléctricos, minería/GNSS, satélites, DMC
Datos y API — registro con llave de API, HAPI, descargas CSV, pronósticos; licencia de cada serie (v0.5.0)
Estado — salud de fuentes y productos
Acerca — HelioUSACH, Plataforma Nacional (enlace), contacto
```

Detailed impacts-by-sector, SUVI, magnetometer network and D-RAP are the
national platform's — link to them, don't rebuild (D-001, D-037).

## 8. Operations & quality

- CI runs lint, tests (every adapter has a recorded-fixture test), manifest
  validation, `makemigrations --check`, `check --deploy`.
- `/estado/` is fed by heartbeats; a stale product shows as such.
- Model cards and, once there is enough live history, a skill score for every
  ML product.
- Nightly `pg_dump` + config bundle (incl. analytics, D-068), pulled by an
  off-machine backup host (D-039).
- Nightly NRT export (`manage.py export_nrt`, D-091): every product's stored
  rows, per UTC day, as gzipped JSON lines in the backup area, pulled with
  the dumps. Models are trained on science data but run on NRT data, so the
  NRT record as it was stored is kept for an archive built off the VM.

## 9. Repository layout (D-010, D-016)

```
climaespacial/   Django project (settings, urls)
core/            product registry, manifest loader/validator, store, swxkit, status
web/             public pages, templates, i18n (es/en), static (vendored chart lib)
api/             API keys and usage, the export layer, HAPI server, downloads (v0.5.0)
modules/         one folder per product family — contributors work here
  swpc/            adapters + manifests + tests + fixtures + templates for SWPC products
  _template/       copy-me module skeleton (and `manage.py startmodule`)
deploy/          systemd units, nginx snippet, deploy scripts, runbooks
docs/            this file, module contract, model-card template, decisions
```

**Module contract (short form):** a module may add manifests,
collectors/services, templates for its own product cards, and tests with
recorded fixtures. It may not edit `core/`, other modules, settings or
`deploy/`. Talks to the store only through `swxkit`. CI checks: lint (ruff),
tests, manifest schema validation, migrations check, `check --deploy`.

**Branching:** `main` protected; PRs from feature branches; CI must pass;
`CODEOWNERS` review for `core/`. Deploy = a tagged release pulled on the VM
by `deploy/deploy.sh`, run by a human (D-011).

## 10. Deliberately out of scope for now

- Own-instrument ingestion (an HTTP ingest endpoint for edge gateways is kept
  in the design, not built) — D-023.
- Public alert subscriptions — the `alerts/` module exists, stays disabled —
  D-020.
- Long-term archive service on separate storage — D-015/D-038.
