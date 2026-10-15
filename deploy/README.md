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
4. Re-deploying a later tag is the same `deploy.sh <tag>` command.

## Files

| File | What |
|---|---|
| `provision.sh` | One-shot server prep (idempotent) |
| `deploy.sh` | Install one tagged release; run again for every release |
| `nginx-clima-espacial.conf` | `/clima-espacial/` location snippet for the existing `helio` vhost |
| `systemd/swx-web.service` | gunicorn |
| `systemd/swx-collect@.service` + `swx-collect@<product>.timer` | one collector run per product, cadence from its manifest |
| `systemd/swx-dump.service` + `.timer` | nightly `pg_dump` + config bundle (local; pulled off-machine, see below) |
| `systemd/goatcounter.service` | cookieless analytics (D-043), bound to localhost |
| `swx-dump.sh` | the nightly backup script itself |
| `backup-pull.sh`, `README-backup.md` | runs on the **off-machine** backup host, not on helio (D-039) |

## Adding a new collected product

Add its manifest under `modules/<name>/manifests/`, register a collector in
`modules/<name>/collectors.py`, then add a matching
`systemd/swx-collect@<product-id>.timer` here with `OnUnitActiveSec` set to
the manifest's `cadence` — `deploy.sh` installs every `*.timer` file it finds
in this directory, so the next deploy picks it up automatically.
