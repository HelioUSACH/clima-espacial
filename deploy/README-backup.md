# Off-machine backups (D-039)

The backup host is **icaria** (donnager cannot reach helio). helio never
pushes (D-039): icaria **pulls**, over a key that can only read `/srv/swx/backups/` and nothing
else on helio. Nightly content of that directory is produced by
`swx-dump.service`/`.timer` (`deploy/swx-dump.sh`), kept 14 days on helio:

| File | Contents |
|---|---|
| `swx-<stamp>.dump.gz` | `pg_dump --format=custom` of the `swx` database |
| `swx-config-<stamp>.tar.gz` | `etc/swx/swx.env`, `modules/` (every deployed manifest), and since v0.3.0 the analytics: `goatcounter-db.sqlite3` (GoatCounter's database, copied with SQLite's online backup API so it's consistent while GoatCounter runs) and `etc/swx/goatcounter-import.env` (its import token) |

If analytics isn't set up yet, the dump logs a warning and the bundle simply
leaves those two out.

Since v0.5.1 the same directory also holds **`nrt/`**, the nightly NRT
export (D-091, see [below](#the-nrt-export-nrt)). The pull already copies
it: `rsync -az` is recursive, and the restricted key reads the whole of
`/srv/swx/backups/`, so nothing changes on icaria or in `authorized_keys`.

On icaria the copy lives in `/data/backups/clima-espacial-usach` and keeps
its **own** history: the pull never deletes a file just because helio has
pruned it. `backup-pull.sh` instead prunes `swx-*` files older than
`SWX_BACKUP_KEEP_DAYS` (default 90), and only after a successful pull, so
a broken link to helio never eats into that history (D-070).

**Treat the backup host as sensitive.** The config bundle in every night's
backup contains `/etc/swx/swx.env` — `SECRET_KEY` and the database
credentials — and the GoatCounter API token, not just public data. Back it
up and restrict access to it the same way you would the VM itself.

## One-time setup

**On icaria** (as the user that will run the pull — a dedicated service
account is fine; it needs write access to `/data/bin` and `/data/backups`):
```
ssh-keygen -t ed25519 -f ~/.ssh/swx-backup-pull -N "" -C "swx-backup-pull@$(hostname)"
mkdir -p /data/bin /data/backups/clima-espacial-usach && chmod 700 /data/backups/clima-espacial-usach
curl -fsSL -o /data/bin/swx-backup-pull.sh https://raw.githubusercontent.com/HelioUSACH/clima-espacial/main/deploy/backup-pull.sh
chmod 700 /data/bin/swx-backup-pull.sh
```
Add to `~/.ssh/config`:
```
Host swx-backup-pull
    HostName helio.fisica.usach.cl
    User swx-backup
    IdentityFile ~/.ssh/swx-backup-pull
```

**On helio** (Victor, sudo — this is the one piece of the backup path that
touches the production VM, so it's a manual step, not a script):
```
groupadd swx-backup
useradd --shell /bin/sh --home-dir /home/swx-backup --create-home --gid swx-backup swx-backup
```
`/bin/sh`, not `nologin`: sshd runs a forced command by exec'ing the user's
**login shell** with `-c '<command>'` — `nologin` ignores that and just
refuses the whole session, so the forced command never runs at all
(confirmed: with `nologin` the client gets "This account is currently not
available." and nothing else; switching to `/bin/sh` is what makes the
`command=` entry below actually execute). The restriction comes entirely
from that forced command + `restrict`, never from the shell.

`swx-dump.sh` already writes `/srv/swx/backups` as `root:swx-backup 750`
(files `640`) once this group exists — nothing else to `chmod`/`chgrp` here.

Ubuntu 24.04's `rsync` package ships `rrsync` ready to use at
`/usr/bin/rrsync` — nothing to extract or install. Add the backup host's
**public** key to `/home/swx-backup/.ssh/authorized_keys` (create that
directory `700`, the file `600`, owned by `swx-backup`), forced to
read-only `rrsync` scoped to the backup directory and nothing else:
```
command="/usr/bin/rrsync -ro /srv/swx/backups/",restrict ssh-ed25519 AAAA...the-backup-host's-public-key...
```
`restrict` (OpenSSH ≥ 7.2) is the modern equivalent of listing
`no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty` by hand.

## Verify

From icaria:
```
SWX_BACKUP_DEST=/data/backups/clima-espacial-usach bash /data/bin/swx-backup-pull.sh
ls -l /data/backups/clima-espacial-usach
```
should copy the dump/config files and the `nrt/` tree, and nothing else. Two checks worth doing
once, by hand, before trusting the schedule (both confirmed against a real
`rrsync` while building this):
- `rsync -az swx-backup-pull:../../etc/passwd /tmp/x` must be **refused**
  ("do not use `..` in arg") — confirms the key can't escape the backup
  directory.
- `rsync` **to** the remote (anything that would write) must be refused
  ("sending to read-only server is not allowed") — confirms `-ro` holds.

If a pull ever fails with `change_dir ".../srv/swx/backups/srv/swx/backups"
failed`, the client asked for the path a second time — `rrsync` already
anchors at `/srv/swx/backups/`, so the client must use a bare `host:` (see
`backup-pull.sh`), not `host:/srv/swx/backups/`.

## Schedule

A daily cron entry on icaria at 04:30, an hour after helio's 03:30 dump
(and the 03:05 NRT export)
(`crontab -e` as the pull user):
```
30 4 * * * SWX_BACKUP_DEST=/data/backups/clima-espacial-usach /bin/bash /data/bin/swx-backup-pull.sh >> /data/backups/clima-espacial-usach/pull.log 2>&1
```
Add `SWX_BACKUP_KEEP_DAYS=<n>` to the same line to keep more or fewer than
90 days. Check `tail /data/backups/clima-espacial-usach/pull.log` weekly.
After a release that changes `deploy/backup-pull.sh`, re-run the `curl`
line above to update the copy in `/data/bin`.

## The NRT export (`nrt/`)

A data server builds an archive (monthly CDF files) from the NRT data the
portal stores. Models are trained on science data but run on NRT data, so
the NRT record *as it was stored* is worth keeping (D-091). The portal's
part is `manage.py export_nrt`, run nightly by `swx-export-nrt.timer` at
03:05 helio time, before the 03:30 dump and well before icaria's 04:30 pull.

**Layout.** One file per product and UTC day, for every product with stored
rows (third-party ones included: these files are for internal use):
```
nrt/<product>/<yyyy>/<product>_<yyyymmdd>.jsonl.gz
nrt/.export_nrt.json        the marker: last successful run, files written
```
e.g. `nrt/noaa.goes.electrons/2026/noaa.goes.electrons_20261018.jsonl.gz`.

**Content.** gzip of JSON lines: one stored row per line, keys sorted,
times ISO 8601 UTC with `Z` (microseconds only when non-zero), `null` where
the column is null. Fields by product kind:

| Kind | Fields | Day file chosen by |
|---|---|---|
| time series / index | `time_tag` (interval start), `source` (spacecraft or route, D-040/D-081), `payload` (the manifest's parameters), `quality`, `provenance`, `inserted_at` (when the value became known), `revised_at` (null unless revised in place, D-080) | `time_tag` |
| event | `issued`, `valid_from`, `valid_to`, `payload`, `quality`, `provenance`, `created_at` | `issued` |
| forecast | `model_version`, `issued_at`, `valid_at`, `lead_seconds`, `site`, `values` (`{field: {value, lower, upper}}`), `provenance`, `inserted_at` | `issued_at` (a whole issue stays in one file) |

Rows are in stored order (`time_tag`, then insertion order: a GOES file has
one row per energy channel per time). Values are raw, as stored: a
provider's fill values are kept, not replaced. What a payload's fields
mean is in the product's manifest (`modules/*/manifests/`, also in each
night's config bundle).

**A day file can be re-written.** Each run re-writes every day that gained
or changed rows since the last successful run (`inserted_at`, `revised_at`
or `created_at` after the marker, with an hour of overlap): today's file,
which is partial until the UTC day ends; late data (Kyoto AE arrives weeks
late, so a file from last month can change); revisions (Dst within 6 h, AE
within 40 days); and backfills. Treat the newest copy of a file as the
truth and rebuild whatever was derived from it. The first run writes every
day there is. The output is deterministic, so an unchanged day keeps the
same bytes and isn't re-written at all. Files are written as `*.tmp` and
renamed; the pull skips `*.tmp`, so it never copies half a file.

**Permissions.** `nrt/` is `swx:swx-backup` `2750` (setgid), files `0640`,
directories `0750`: readable by the pull key's group, not world-readable,
like the dumps. The export runs as `swx`; it reaches `nrt/` through the
root-owned `0750` backup directory because its systemd unit gives that
one process `swx-backup` as a supplementary group, and the unit's
`ExecStartPre` (run as root) creates both directories with these owners.
`swx` itself is not in `swx-backup`, so it still can't read the dumps.
**The `swx-backup` group must exist** (the one-time setup below); without
it the unit fails to start, the timer stays active, and
`systemctl status swx-export-nrt` says why.

**Retention.** helio deletes these files 30 days after they were last
written (`SWX_NRT_KEEP_DAYS`), and the stored rows themselves are never
deleted, so a file that changes later is simply written again. icaria
keeps everything: `backup-pull.sh` never deletes, and its prune touches
only top-level `swx-*` files. `swx-dump.sh`'s prune is top-level only too.

**Check it.** On helio, after the first night (or right away with
`sudo systemctl start swx-export-nrt`):
```
systemctl status swx-export-nrt --no-pager    # "export_nrt: N written, ..."
sudo ls -l /srv/swx/backups/nrt/ /srv/swx/backups/nrt/noaa.kp/2026/ | head
sudo cat /srv/swx/backups/nrt/.export_nrt.json
sudo zcat /srv/swx/backups/nrt/noaa.kp/2026/noaa.kp_$(date -u +%Y%m%d).jsonl.gz | head -3
```
On icaria, after the next 04:30 pull:
```
ls /data/backups/clima-espacial-usach/nrt/
find /data/backups/clima-espacial-usach/nrt -name '*.jsonl.gz' | wc -l
zcat /data/backups/clima-espacial-usach/nrt/noaa.kp/2026/noaa.kp_$(date -u +%Y%m%d).jsonl.gz | head -3
```

## Restoring analytics

```
sudo systemctl stop goatcounter-import goatcounter
sudo rm -f /srv/swx/goatcounter/db.sqlite3-wal /srv/swx/goatcounter/db.sqlite3-shm   # stale WAL must not replay onto the restored file
tar xzf swx-config-<stamp>.tar.gz goatcounter-db.sqlite3 etc/swx/goatcounter-import.env
sudo install -o swx -g swx -m 640 goatcounter-db.sqlite3 /srv/swx/goatcounter/db.sqlite3
sudo install -o root -g swx -m 640 etc/swx/goatcounter-import.env /etc/swx/goatcounter-import.env
sudo systemctl start goatcounter goatcounter-import
```

## Restore drill

Once per semester (docs/ARCHITECTURE.md §8): on a scratch machine,
`pg_restore` the latest pulled `.dump.gz` into a fresh database and confirm
`manage.py check` passes against it.
