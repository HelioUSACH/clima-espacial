# Clima Espacial USACH: instructions for AI coding sessions

This file is the contract for any AI session (Claude Code or otherwise)
working in this repository. `CLAUDE.md` just points here.

**Before any task:** read `README.md`, then `docs/ARCHITECTURE.md`, then
`docs/DECISIONS.md` to check nothing here has since been superseded. If your
task touches a module, also read `docs/modules/README.md` and that module's
own docs. If it touches a model integration, read `docs/models/INTEGRATION.md`.
A model lives in its own repo; its agent runs `docs/models/CONTRIBUTING-A-MODEL.md`.

## Hard rules

- **Stack:** Django 5.2 LTS + PostgreSQL 16 + HTMX; server-rendered templates;
  **no JS build step, no CDN** — vendor any JS/CSS/font file into the repo
  instead. Background work is a management command run by a systemd timer
  (no Celery, no Redis, no Docker). Don't introduce another framework or
  runtime without a new entry in `docs/DECISIONS.md`.
- **Everything is a product.** A new data series, event stream or model lives
  behind a product manifest (`docs/ARCHITECTURE.md` §4) and is published only
  through `swxkit` (`core/swxkit.py`). Never write to the product tables
  directly from a module.
- **Module boundary.** Code under `modules/<name>/` may add manifests,
  collectors/services, templates for its own product cards, and tests with
  recorded fixtures. It may **not** edit `core/`, another module, `climaespacial/`
  (settings) or `deploy/`. Copy `modules/_template/` or run
  `manage.py startmodule <name>` to start one.
- **External adapters degrade, they don't break the portal.** Every adapter
  that talks to an outside source (SWPC, Kyoto, JSOC, …) ships with: a
  recorded fixture of a real payload, a schema test, and a freshness rule
  (`stale_after` in the manifest). A provider change must turn one product
  stale/red, not crash the site — see `docs/DECISIONS.md` D-040 for why this
  matters (NOAA SWPC changed its JSON shape under us once already).
- **Spacecraft/source names are data, never hard-coded.** The active L1
  monitor (`SOLAR1`, `DSCOVR`, `ACE`, …) changes over time; read it from the
  payload's `source`/`active` fields.
- **Bilingual:** Spanish primary, English secondary (Django i18n). Add
  strings to both `.po` files in the same change.
- **Never write a `{# #}` comment that spans more than one line** in a
  template — Django's `{# #}` only works on a single line; across a line
  break it is printed as literal page text instead of being stripped (this
  broke every chart on the real v0.1.1 deploy — the leaked text contained a
  `<script>` tag that swallowed the real one). Use
  `{% comment %}…{% endcomment %}` for a multi-line comment.
  `manage.py check_templates` (CI) catches this.
- **Licensing:** this repo is Apache-2.0, data products are CC BY 4.0 — never
  commit a dependency or vendored asset with an incompatible licence.
- **No personal or internal data in this repo.** No real API keys, no server
  IPs, no internal grant/project codes, no people's private notes. Fixtures
  use real *public* provider payloads (SWPC et al. publish openly) or
  synthetic data, never anything from a private planning record.
- **Privileged operations (deploy, systemd, anything needing `sudo` on the
  server) are scripts for a human to run** (`deploy/`), never executed by an
  AI session.

## When a task is done

Record any new decision in `docs/DECISIONS.md` (append a row, never rewrite
one) and update the relevant doc (`docs/ARCHITECTURE.md`, a module's own
docs, or a model card). Don't leave scratch/session logs in the repo.

## Related

- `docs/ARCHITECTURE.md` — product contract, module contract, deployment shape.
- `docs/modules/README.md` — how to add a module.
- `docs/models/INTEGRATION.md` — how to integrate a model that lives in its
  own repository.
- `docs/DECISIONS.md` — append-only decision log; later entries win on conflict.
