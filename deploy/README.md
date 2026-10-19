# Deploying Clima Espacial USACH

Order of operations for a first deploy on the helio VM (D-005, D-011 — run
by Victor; nothing here is run by an AI session, AGENTS.md).

1. `sudo bash provision.sh` — swapfile, PostgreSQL role/db, `swx` user and
   directories, `/etc/swx/swx.env` skeleton, GoatCounter binary. Idempotent.
2. Follow provision.sh's printed manual steps: install the nginx snippet and
   add the one `include` line to the existing `helio` vhost
   (`nginx-clima-espacial.conf`), then create the first GoatCounter site.
3. Tag a release in git, then on the server: `sudo bash deploy.sh v0.1.0`
   (`deploy.sh`) — clones that tag straight from GitHub (never from a local
   working copy), installs it to `/opt/swx`, migrates, collects static
   files, installs/starts the systemd units, smoke-tests.
4. Re-deploying a later tag is the same `deploy.sh <tag>` command. When a
   release changes `nginx-clima-espacial.conf`, also re-install the snippet
   and reload nginx (`CONTRIBUTING.md` → Releasing).

### Why deploy.sh hands over to the tag's own copy (D-077)

`sudo bash /opt/swx/deploy/deploy.sh <tag>` starts the script of the release
that is *currently installed*. Its rsync replaces that file, but bash keeps
reading the copy it opened, so before 0.4.1 every later step (venv, migrate,
units, checks) was the previous release's logic: v0.4.0 installed
`swx-verify.timer` and never enabled it. Since 0.4.1 the script clones the
tag and immediately runs the clone's `deploy/deploy.sh`, which does the
install. It prints "Re-running <tag>'s own deploy.sh". Rolling back to an
older tag works the same way: that tag's script, which predates the
hand-over, clones again and cleans up after itself. **The first deploy of
v0.4.1 still runs v0.4.0's script**, which is harmless (v0.4.1 enables no new
unit), so the hand-over and the timer checks below take effect from the
deploy after it.

After installing the units, the script enables every `systemd/*.timer` (no
hand-kept list), then fails the deploy if any of them is not enabled and
active, and prints `systemctl list-timers 'swx-*'` with the next and last
runs. Before that, an installed `swx-*.timer` that the release no longer
ships (a retired product, e.g. `swx-collect@noaa.dst.timer` in 0.4.2) is
disabled and removed (D-081).

## One-off steps after a deploy

Some releases need a command run once after `deploy.sh`, as the service
user with the production environment:

```bash
cd /opt/swx && sudo -u swx bash -c 'set -a; . /etc/swx/swx.env; set +a; venv/bin/python manage.py <command>'
```

| release | command | what |
|---|---|---|
| 0.4.2 | `backfill_kyoto_dst --days 30` | 30 days of Kyoto's realtime Dst into `kyoto.dst` (D-081). Safe to re-run: it also repairs hours Kyoto has revised. |
| 0.4.2 | `backfill_kyoto_ae --since 2026-01-01` | `kyoto.ae` history, a month per request, ~1440 rows a day (D-082). Takes a few minutes. Safe to re-run. |

## Data access: keys, HAPI, downloads (v0.5.0, D-083..D-090)

- **Email (optional).** To email new API keys, add the `SWX_EMAIL_*`
  variables from `.env.example` to `/etc/swx/swx.env` and restart `swx-web`.
  Without `SWX_EMAIL_HOST` nothing is sent and the key is only shown on
  screen. Check outbound SMTP from the server first (`nc -vz <host> 587`).
- **Limits** default to the values in `.env.example`; override any of them
  in `/etc/swx/swx.env`. The per-minute and keyless per-hour throttles live
  in each gunicorn worker's memory (approximate, up to 2×); the daily quotas
  are counted in the database (exact).
- **Logs never carry an API key.** gunicorn's access log records the path
  without the query string (`--access-logformat` in
  `systemd/swx-web.service`), nginx doesn't log `/clima-espacial/hapi` and
  `/clima-espacial/api/` (`access_log off`), and GoatCounter's import skips
  both. Check after deploying: `sudo journalctl -u swx-web -n 50` shows
  paths with no `?`.
- **Old registrations.** People who registered before v0.5.0 have no key:
  in `/gestion/` → Registration requests, select them and run "Issue an API
  key". The key is shown once in the admin message (and emailed if SMTP is
  set). Service keys (e.g. for the national platform) are added under API
  keys → Add, with a label and optional quotas; the key is shown once.
- **Usage report** for the project's reporting, by sector, no personal data:
  `manage.py api_usage_report --from 2026-10-01 --to 2026-12-31 [--by day]`
  (run it like the one-off commands below).
- **HAPI verifier.** `bash deploy/hapi-verify.sh` (from any machine with git,
  Node.js ≥ 16, npm, curl and python3; no sudo) runs the community verifier
  against the production server, one dataset per clock hour because the
  verifier can't send a key and the keyless preview allows 30 requests an
  hour. Reports land in `./hapi-verify-<dataset>.txt`.

## Caching and compression (D-055, D-057)

- `/clima-espacial/static/`: hashed filenames from `collectstatic`
  (`ManifestStaticFilesStorage`), served `expires max` +
  `Cache-Control: public, immutable` — a file's URL changes whenever its
  content does, so browsers never mix an old `charts.js` with new HTML.
- HTML pages: `Cache-Control: no-cache` (always revalidated).
- `serie.json`: Django's own `Cache-Control: public, max-age=60`, and a
  60-s server-side cache per (product, range).
- HAPI and `/api/v1/` responses set their own `Cache-Control` (metadata
  `public, max-age=60`; data, downloads and the key page `no-store`), so
  their nginx locations add none.
- gzip for JSON/JS/CSS/SVG/CSV (HTML is gzipped by nginx already). The `gzip*`
  lines sit in the helio `server{}` context, so they apply to the group
  site's responses too — compression only, nothing else changes there.
- nginx gotcha worth knowing: a `location` that has its own `add_header`
  stops inheriting **every** `add_header` set at the `server{}` level. If the
  helio vhost sets security headers with `add_header` at server level, they
  no longer reach `/clima-espacial/` responses (Django sends its own HSTS,
  nosniff, Referrer-Policy and X-Frame-Options, so nothing is lost today).

## gunicorn: threads instead of processes (applied in v0.3.0)

`swx-web.service` runs `--workers 2 --threads 4 --worker-class gthread`.
Measured locally on v0.1.3 with the same seeded data, after the same
warm-up requests: **3 sync workers = 192 MB RSS (4 processes); 2 gthread
workers × 4 threads = 133 MB (3 processes)**, about 60 MB less, and 8
concurrent requests instead of 3. The app is I/O-bound (PostgreSQL), so
threads are a good fit, and it frees memory before the models arrive on the
same VM. After deploying, watch `systemctl status swx-web` (memory line)
for a day. Note that the `serie.json` cache (D-057) is per *process*, so
fewer processes also means fewer cold caches.

## Files

| File | What |
|---|---|
| `provision.sh` | One-shot server prep (idempotent) |
| `deploy.sh` | Install one tagged release; run again for every release |
| `nginx-clima-espacial.conf` | `/clima-espacial/` location snippet for the existing `helio` vhost |
| `systemd/swx-web.service` | gunicorn |
| `systemd/swx-collect@.service` + `swx-collect@<product>.timer` | one collector run per product, cadence from its manifest (`kyoto.ae`: daily 04:10 UT) |
| `systemd/swx-verify.service` + `.timer` | daily 03:15 UT `manage.py verify_forecasts`: scores every forecast product (D-075) |
| `systemd/swx-dump.service` + `.timer` | nightly `pg_dump` + config bundle incl. analytics (local; pulled off-machine, see below) |
| `systemd/goatcounter.service` | cookieless analytics (D-043), bound to localhost |
| `systemd/goatcounter-import.service` | imports pageviews from nginx's access log (no browser JS, D-051); skips `/hapi/` and `/api/` |
| `hapi-verify.sh` | runs the HAPI verifier against the server (not CI; no sudo) |
| `swx-dump.sh` | the nightly backup script itself |
| `backup-pull.sh`, `README-backup.md` | runs on the **off-machine** backup host, not on helio (D-039) |

## Adding a new collected product

Add its manifest under `modules/<name>/manifests/`, register a collector in
`modules/<name>/collectors.py`, then add a matching
`systemd/swx-collect@<product-id>.timer` here with `OnUnitActiveSec` set to
the manifest's `cadence` — `deploy.sh` installs every `*.timer` file it finds
in this directory, so the next deploy picks it up automatically.

A forecast product's timer is the same, but use `OnCalendar` aligned to the
issue cadence rather than `OnUnitActiveSec`, so the run lands after the
boundary its `issued_at` is floored to. Leave room for the inputs to arrive:
`usach.kp.persistence` runs at `*-*-* *:35:00` because a Kp interval reaches
the store 5–16 min after it closes (D-079; see
`swx-collect@usach.kp.persistence.timer`).
