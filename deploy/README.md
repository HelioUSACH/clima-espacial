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

## Caching and compression (D-055, D-057)

- `/clima-espacial/static/`: hashed filenames from `collectstatic`
  (`ManifestStaticFilesStorage`), served `expires max` +
  `Cache-Control: public, immutable` — a file's URL changes whenever its
  content does, so browsers never mix an old `charts.js` with new HTML.
- HTML pages: `Cache-Control: no-cache` (always revalidated).
- `serie.json`: Django's own `Cache-Control: public, max-age=60`, and a
  60-s server-side cache per (product, range).
- gzip for JSON/JS/CSS/SVG (HTML is gzipped by nginx already). The `gzip*`
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
| `systemd/swx-collect@.service` + `swx-collect@<product>.timer` | one collector run per product, cadence from its manifest |
| `systemd/swx-dump.service` + `.timer` | nightly `pg_dump` + config bundle incl. analytics (local; pulled off-machine, see below) |
| `systemd/goatcounter.service` | cookieless analytics (D-043), bound to localhost |
| `systemd/goatcounter-import.service` | imports pageviews from nginx's access log (no browser JS, D-051) |
| `swx-dump.sh` | the nightly backup script itself |
| `backup-pull.sh`, `README-backup.md` | runs on the **off-machine** backup host, not on helio (D-039) |

## Adding a new collected product

Add its manifest under `modules/<name>/manifests/`, register a collector in
`modules/<name>/collectors.py`, then add a matching
`systemd/swx-collect@<product-id>.timer` here with `OnUnitActiveSec` set to
the manifest's `cadence` — `deploy.sh` installs every `*.timer` file it finds
in this directory, so the next deploy picks it up automatically.
