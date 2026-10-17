# Contributing

Clima Espacial USACH grows by **adding product modules without touching the
core** — see `docs/ARCHITECTURE.md` for the full picture. This file is the
human-readable checklist; `AGENTS.md` is the equivalent contract for AI
coding sessions (several contributors use one).

## Setup

```
python3 -m venv .venv && source .venv/bin/activate   # or ~/.venv-cache/clima-espacial
pip install -r requirements-dev.txt
cp .env.example .env                                  # fill in SECRET_KEY, DATABASE_URL
createuser swx && createdb -O swx swx                 # local PostgreSQL 16
python manage.py migrate
python manage.py seed_dev_data --days 7     # optional: 7 days of synthetic charts data (dev only)
python manage.py runserver
```

## Translations (ES primary, EN secondary — D-008)

Source strings are written in Spanish (`{% trans %}`/`{% blocktrans %}` in
templates); `locale/en/LC_MESSAGES/django.po` holds the English translation.
After adding or changing a string:

```
sudo apt-get install gettext   # once, if not already present
python manage.py makemessages -l en --add-location=never
# fill in the new msgstr entries in locale/en/LC_MESSAGES/django.po
python manage.py compilemessages
```

## Adding a module (the common case)

1. `python manage.py startmodule <name>` — copies `modules/_template/`.
2. Write a product manifest per product (`docs/ARCHITECTURE.md` §4.1) and a
   collector/service that publishes through `core/swxkit.py`.
3. Record a real fixture of the provider's payload under
   `modules/<name>/tests/fixtures/`; write a schema test against it.
4. Set `stale_after` on the manifest so a dead source degrades to "stale"
   instead of silently showing old data.
5. `python manage.py validate_manifests` must pass.

A module may **not** edit `core/`, another module, `climaespacial/` (settings)
or `deploy/` — see `CODEOWNERS`.

## Adding a model

See `docs/models/INTEGRATION.md`. Models live in their own repository; this
repo only needs a manifest, a producer following the product contract, and a
model card (`docs/models/MODEL-CARD-TEMPLATE.md`).

## Vendored assets (charts, fonts) — D-002, never a CDN

- **ECharts** (`web/static/web/vendor/echarts/`): see that directory's own
  `README.md` for the trimmed build's source and rebuild instructions.
  Rebuild only when upgrading ECharts itself.
- **Fonts** (`web/static/web/fonts/`, `web/static/web/css/fonts.css`):
  Bebas Neue, Poppins, Roboto, JetBrains Mono, fetched as woff2 from the
  `@fontsource/*` npm packages and vendored with each face's own `OFL.txt`.
  To add a weight or subset, `npm i @fontsource/<font>` somewhere scratch,
  copy the needed `files/*-latin[-ext]-<weight>-normal.woff2`, and add the
  matching `@font-face` rule (with its package's own `unicode-range`) to
  `fonts.css`. npm is a one-time fetch tool here, never a runtime or build
  dependency of the portal itself.

## PR checklist

- [ ] `ruff check .` clean
- [ ] `pytest` passes, including a fixture-backed test for any new adapter
- [ ] `python manage.py check_templates` passes (no multi-line `{# #}`
      comments — these are printed as literal page text instead of being
      stripped; a real v0.1.1 production bug, see `docs/DECISIONS.md` D-052)
- [ ] `python manage.py validate_manifests` passes
- [ ] `python manage.py makemigrations --check` clean (no missing migrations)
- [ ] New strings added to both locales (`es` primary, `en` secondary)
- [ ] A new decision, if any, appended to `docs/DECISIONS.md` (never edit a
      past row)
- [ ] Nothing under `core/`, `climaespacial/` or `deploy/` changed unless the
      PR is explicitly a core change (needs `CODEOWNERS` review)

CI (`.github/workflows/ci.yml`) runs lint, tests, manifest validation,
`makemigrations --check` and `check --deploy` on every PR (job `test`), then
a real browser against seeded data (job `screenshots`, D-058): home and the
`noaa.rtsw.mag` page at 1440/390 px × day/night × ES/EN. Its screenshots
are uploaded as the run's `screenshots` artifact — **look at them before
approving a PR that touches templates, CSS or JS**. The job fails if a
chart is missing, a panel shows its error/empty state, the page logs a JS
error, or the range buttons regress (16-PERF B1/B2). To run it locally:
`pip install playwright==1.55.0 && python -m playwright install chromium`,
start `runserver` on seeded data, then
`python .github/scripts/screenshots.py http://127.0.0.1:8000 screenshots/`.

## Branching: every change goes through a PR

**Every change to `main` goes through a pull request** — no direct pushes,
including by the maintainer and by AI sessions. Work on a branch
(`fix/…`, `feat/…`), open a PR, wait for both CI jobs and the review, merge.
(v0.1.2 landed as two direct commits on `main` with no PR and no
screenshots; that is how a broken release reached the server, 13-AUDIT §2.)

**Branch protection (one-time, maintainer):** on GitHub, open the repository
→ **Settings** → **Rules** → **Rulesets** → **New ruleset** → **New branch
ruleset**, then:

- *Ruleset name:* `main`; *Enforcement status:* **Active**
- *Target branches* → **Add target** → **Include default branch**
- tick **Restrict deletions** and **Block force pushes**
- tick **Require a pull request before merging** (required approvals: 0 is
  fine while there's one maintainer — the PR itself is the gate)
- tick **Require status checks to pass** → **Add checks** → add `test` and
  `screenshots` (they appear in the search once CI has run on a PR), and
  tick **Require branches to be up to date before merging**
- **Create**

(Direct link: `https://github.com/HelioUSACH/clima-espacial/settings/rules`.
The older equivalent is **Settings** → **Branches** → **Add classic branch
protection rule** for `main` with the same options.)

## Releasing

1. Merge the PR; bump `version` in `pyproject.toml` in that PR.
2. Tag the merge commit: `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. On the server: `sudo bash /opt/swx/deploy/deploy.sh vX.Y.Z`. Its smoke
   test checks `/estado/`, `/`, one `serie.json` and that the hashed static
   files the page references exist.
4. If `deploy/nginx-clima-espacial.conf` changed: copy it to
   `/etc/nginx/snippets/clima-espacial.conf`, `sudo nginx -t`, then
   `sudo systemctl reload nginx`.
5. **After every deploy, a hard-refresh check** (Ctrl+Shift+R / Cmd+Shift+R)
   of the home page and one product page, in both themes and both
   languages: every chart drawn, no unstyled header, the toggle works.
   Hashed static files (D-055) mean a normal visitor never needs this —
   it's to catch what the smoke test can't see.

## For the maintainer: keep `.git` outside OneDrive

This repository's working tree lives inside a OneDrive-synced folder that
also doubles as the private planning record (`.gitignore` keeps the two from
mixing — see `docs/DECISIONS.md` D-047). That's fine for the working tree,
but **the `.git` directory itself should not live there**: the Department's
own platform repo lost its history twice this way (OneDrive syncing mid-write
corrupts git's object store). Move it out once, from WSL:

```
mkdir -p ~/repos
git init --separate-git-dir ~/repos/clima-espacial.git
```

(the parent directory must already exist — `git init` won't create it) run
from the repo root — this relocates `.git` to a plain Linux path while
the working tree (and its sync with the private planning files) stays
exactly where it is. Never run `git clean -x` (or `-f` without checking
`git status` first) in this folder: an untracked file here might be a
private planning document, not build cruft.
