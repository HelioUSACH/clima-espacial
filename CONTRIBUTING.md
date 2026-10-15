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

## PR checklist

- [ ] `ruff check .` clean
- [ ] `pytest` passes, including a fixture-backed test for any new adapter
- [ ] `python manage.py validate_manifests` passes
- [ ] `python manage.py makemigrations --check` clean (no missing migrations)
- [ ] New strings added to both locales (`es` primary, `en` secondary)
- [ ] A new decision, if any, appended to `docs/DECISIONS.md` (never edit a
      past row)
- [ ] Nothing under `core/`, `climaespacial/` or `deploy/` changed unless the
      PR is explicitly a core change (needs `CODEOWNERS` review)

CI (`.github/workflows/ci.yml`) runs lint, tests, manifest validation,
`makemigrations --check` and `check --deploy` on every PR.

## Branching

`main` is meant to stay protected (GitHub branch protection isn't actually
configured yet — until it is, treat this as a convention, not an enforced
rule). Work on a feature branch, open a PR, wait for CI and the required
review. Deploys are a tagged release pulled onto the server by a human
running `deploy/deploy.sh` — never a direct push to the server.

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
