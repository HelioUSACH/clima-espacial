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
 future sources  ┘            future collectors/services             │   PostgreSQL 16              │ ─► Public REST + HAPI (M2)
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
kind: event                    # timeseries | event | image | bulletin | index | status
title: {es: "Detección de fulguraciones (NRT)", en: "Flare detection (NRT)"}
maturity: beta                 # experimental | beta | operational
cadence: 180s
stale_after: 15m
producer: {type: service, unit: swx-flare-loop, repo: HelioUSACH/flare-nrt}
source: {provider: "HelioUSACH (USACH)", inputs: ["SDO/AIA synoptic via JSOC"], license: "CC-BY-4.0"}
parameters:
  - {name: probability, units: "1", range: [0, 1]}
  - {name: state, enum: [QUIET, RISING, POST_IMPULSIVE]}
exchange: {hapi: true, feed: true, push: [national-platform]}   # what may leave the portal
model_card: docs/products/usach.flare.nrt.md
description: {es: "...", en: "..."}   # optional: the product page's "Qué muestra" text
facts:                                # optional: the product page's "Ficha del producto" <dl>
  source: {es: "...", en: "..."}      # recognised keys: source, spacecraft, cadence,
  license: {es: "...", en: "..."}     # coordinates, license, used_by -- a product skips
                                       # any row it doesn't set, never shows one empty
```

Validated against `core/manifests/schema.json` by `manage.py
validate_manifests` (also run in CI). See `docs/modules/README.md` for how a
module ships its manifests.

### 4.2 Publishing API for producers (`core/swxkit.py`)

- `publish_timeseries(product, rows, *, quality=None, provenance=None)` —
  `rows` is an iterable of plain dicts, one key per manifest parameter plus
  `time_tag`; no pandas dependency.
- `publish_event(product, payload, *, valid_from, valid_to=None, quality=None, provenance=None)`.
- `heartbeat(product, status, *, detail=None)` — `status` is one of `ok`,
  `stale`, `error`, `no_data`; fed into `/estado/`.

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

### 4.4 Outbound exchange (M2, D-013)

1. A read-only **HAPI 3.x** server for `exchange.hapi: true` time series plus
   a JSON **event feed** — no agreement needed, COSPAR-recommended.
2. A **push adapter**: HTTPS POST of the envelope, HMAC-signed, per-subscriber
   product filter.
3. Whatever the national platform's own ingest API specifies (D-041), added
   as a new adapter — core untouched.

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
systemd: swx-web, swx-collect@<product>.timer, swx-dump.timer
          (+ the flare repo's own units, started independently)
```

Indicative RAM budget (15 GiB): PostgreSQL 2–3 GB · gunicorn 3 workers ~0.6 GB
· flare loop (CPU) 2–3 GB · collectors <0.5 GB · OS/cache rest. Disk (96 GB
–4 GB swap): keep a rolling window of heavy files on the VM; long-term
archive is a separate concern (D-015/D-038), out of scope for M1.

## 7. Information architecture (public portal, v1 — stakeholders first, D-034/D-037)

```
Inicio — franja de contexto en vivo (escalas G/S/R, viento solar L1 + nave fuente, Kp, Dst, rayos X, protones)
         + oferta USACH al frente: fulguraciones NRT (beta), modelos y productos, "qué ofrecemos"
Productos y modelos — catálogo con madurez (beta/operacional), hoja de ruta
Qué ofrecemos — para operadores eléctricos, minería/GNSS, satélites, DMC
Datos y API — registro simple, licencia CC BY 4.0, cómo citar (stub in M1)
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
- Nightly `pg_dump` + config, pulled by an off-machine backup host (D-039).

## 9. Repository layout (D-010, D-016)

```
climaespacial/   Django project (settings, urls)
core/            product registry, manifest loader/validator, store, swxkit, status
web/             public pages, templates, i18n (es/en), static (vendored chart lib)
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
