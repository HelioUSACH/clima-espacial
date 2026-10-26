# Contributing a model

For people (and their coding agents) who build a model in **its own
repository** and want it to run as a product of this portal. The portal side
of the integration is in [`INTEGRATION.md`](INTEGRATION.md) (this directory);
this file is about the model's repository. Read both.

## How it fits together

- The model lives in its own repo under the HelioUSACH organisation, never
  inside this one. This repo never imports its code (D-044).
- It runs as a **service** on a host the maintainers choose, listening on
  `127.0.0.1` only, and exposes its latest result over HTTP. A small module
  in this repo polls that endpoint and publishes the result (event or
  forecast) through `core/swxkit.py`.
- A model that only needs data the portal already stores can instead read
  them from the portal's HAPI server or `swxkit.read_series` and run as a
  timer; ask before choosing this shape.
- The maintainers pull your repo onto the server. You never deploy, and
  nothing in your repo may assume root or a particular machine.

## Before you start: fill in the integration sheet

Copy `INTEGRATION-SHEET-TEMPLATE.md` from this directory to the root of your
repo as `INTEGRATION.md` and fill it in. It is what the maintainers review
first. Questions it can't answer yet are fine: write "unknown" and why.

## Checklist: run it before every push

An agent working in the model repo should go through every item, fix what
fails, and report the result in the commit or PR description (item number,
pass/fail, what was done).

### A. What goes to GitHub
1. `git status` shows no data, outputs, caches, logs, notebooks with
   outputs, or virtual environments. A `.gitignore` covers them.
2. No file in the repo is larger than 5 MB; the repo has no history of large
   files either (check with `git rev-list --objects --all` and file sizes).
3. **Weights are never committed.** They ship as a GitHub release asset
   (or another location the maintainers approve), with a SHA-256 next to
   them, and the code downloads and verifies them.
4. No secrets: no tokens, passwords, API keys, private hostnames or IP
   addresses, personal emails, grant names or codes. Configuration comes
   from environment variables with an `.env.example` that has no values.
5. No backup copies (`*.bak*`, `*_old.py`, `*-<machine>.*`), no
   "v2/v3" duplicates of the same script: one current version, history in
   git.
6. The repo is **not** in a cloud-synced folder (OneDrive, Dropbox, Google
   Drive). Two machines writing one synced folder corrupts caches and model
   files.

### B. Reproducible
7. `README.md` says, in order, how to install, fetch the weights, run the
   service, and run the tests, on a fresh Ubuntu 24.04 machine with no GPU.
8. Dependencies are pinned: a ranges file (`requirements.txt` or
   `pyproject.toml`) **and** a lock file with exact versions
   (`pip freeze > requirements.lock.txt` from a working install).
9. The code runs from any directory: no hard-coded home paths, every path
   is a setting with a default under the repo or a `--data-dir`.
10. The training code and its configuration are in the repo, or the README
    says exactly where they are; the model card names the training period.

### C. Tested
11. `pytest` runs offline in under 5 minutes on a CPU and passes.
12. At least one **end-to-end test**: one stored input (small fixture in
    `tests/fixtures/`) through the full inference path to a known output
    within a stated tolerance. If the real weights are too big for CI, the
    test uses a tiny stand-in model and a second, documented test runs with
    the real weights locally.
13. Tests for the parts that decide what users see: thresholds, state
    machines, any rule that turns a score into "detected"/"alert".
14. A linter runs clean (e.g. `ruff check .`).

### D. The HTTP contract (what the portal reads)
15. One endpoint returns the **latest result** as JSON, e.g. `/api/status`
    or `/api/latest`. Its shape is written down in `INTEGRATION.md` (field,
    type, units, meaning). Changing it is a breaking change: say so in the
    PR and tell the maintainers before merging.
16. Every time is ISO 8601 UTC with `Z` (`2026-10-24T18:03:00Z`).
17. The response says how fresh it is: when the result was made, the time
    of the newest input it used, and a status (`live` / `stale` / `no_data`
    / `error`) computed **on every request**, not cached. A model whose
    inputs are old must say `stale`, not keep reporting its last value as
    current.
18. A field the model itself doesn't trust (exploratory, low accuracy) is
    labelled as such in the response, and never mixed into the main
    signal.
19. Forecast models also follow this directory's
    [`INTEGRATION.md`](INTEGRATION.md#forecast-models) "Forecast models": one
    issue per cadence step, fixed horizons, the model version with every
    issue, bounds only if they are real.
20. The service listens on `127.0.0.1` by default and needs no
    authentication from localhost. Any human dashboard is internal too.

### E. Inputs
21. Every input is fetched over **HTTPS** where the provider offers it.
22. Every input's own timestamp is checked: too old, or inconsistent between
    channels/sources, means the frame is skipped and the status says why.
23. The model records which inputs each result used (provider timestamps),
    so any result can be rebuilt later.
24. If the model needs data the portal doesn't have yet, it's in the
    integration sheet (source, cadence, volume, licence). Don't add a
    collector to the portal yourself; the maintainers do.

### F. Runs well on a shared server
25. Runs on CPU. State the RAM (peak) and CPU time per run in the
    integration sheet, measured, not guessed.
26. Disk use is bounded: every output directory has a retention rule
    (the code prunes, or a documented timer does). State the steady-state
    size.
27. Survives restarts: state needed after a restart is on disk; a restart
    doesn't duplicate or lose results.
28. Logs go to stdout/stderr (systemd collects them), not to growing files.
29. Example systemd units are in `deploy/`, with `User=` a dedicated
    unprivileged user, no `sudo` in scripts, and paths as settings.
30. Loading a model file: verify its SHA-256 before `torch.load`; use
    `weights_only=True` for anything that is plain tensors.

### G. Honest documentation
31. `docs/model-card.md` follows this repo's
    [`docs/models/MODEL-CARD-TEMPLATE.md`](MODEL-CARD-TEMPLATE.md):
    performance **against a named baseline**, evaluation period, known
    limitations. The maintainers copy it to `docs/products/<id>.md` here.
32. `CHANGELOG.md` entry for every version; the version string is the one
    the service reports.

## Opening a release for integration

When the checklist passes: tag a version (`v0.1.0`), attach the weights to
the GitHub release with their SHA-256, and tell the maintainers the tag. They
review `INTEGRATION.md`, pull the tag onto the server, and write the portal
side.
