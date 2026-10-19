# Clima Espacial USACH

**Clima Espacial USACH** is HelioUSACH's space-weather portal and data
service — the USACH node of the **Plataforma Nacional de Meteorología
Espacial**. It shows the space-weather conditions that matter for Chile, with
emphasis on what HelioUSACH produces itself (an NRT flare detector, and more
to come), and publishes its products to the national platform through a
versioned, documented interface.

Live at `https://helio.fisica.usach.cl/clima-espacial/` (once deployed).

## How it's built

- **Everything is a product.** A solar-wind series, a flare detection, a
  future geomagnetic index: each is a *product* with a YAML manifest (ID,
  version, schema, provenance, freshness rule). UI, public API and outbound
  exchange all read the same product store.
- **Producers are plugins.** Collectors and model services publish through
  one small contract (`core/swxkit.py`) and live under `modules/<name>/`,
  which may not touch `core/`, other modules, or settings.
- **Boring, shared stack:** Django 5.2 LTS + PostgreSQL 16 + HTMX,
  server-rendered templates, no JS build step, no CDN assets, systemd
  (no Docker/Celery/Redis).
- **Degrade, don't break.** Every external adapter has a freshness rule; a
  stale or failed source shows as such without taking the rest of the portal
  down.

Full design in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The decision
log (append-only, binding) is [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Repository layout

```
climaespacial/   Django project (settings, urls)
core/            product registry, manifest loader/validator, store, swxkit, status
web/             public pages, templates, i18n (es/en), vendored static assets
modules/         one folder per product family — contributors work here
  swpc/            NOAA SWPC adapters (solar wind, indices, X-rays, protons, electrons, scales)
  _template/       copy-me module skeleton
deploy/          systemd units, nginx snippet, deploy scripts (run by a human)
docs/            architecture, module contract, model-card template, decisions
```

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for setup and the PR checklist, and
[`AGENTS.md`](AGENTS.md) if you (or your AI session) are adding a module.

## Licence

Code is [Apache-2.0](LICENSE). Data products are CC BY 4.0 — see
[`docs/DECISIONS.md`](docs/DECISIONS.md) D-028 and each product's manifest
for attribution details.
