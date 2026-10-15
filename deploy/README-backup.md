# Off-machine backups (D-039)

helio cannot reach the backup host (donnager, icaria as fallback) on port
22 (VM preflight, 2026-10-15) — so the backup host **pulls**, over a key
that can only read `/srv/swx/backups/` and nothing else on helio. Nightly
content of that directory is produced by `swx-dump.service`/`.timer`
(`deploy/swx-dump.sh`): a `pg_dump` + a config bundle, 14-day local retention.

**Treat the backup host as sensitive.** The config bundle in every night's
backup contains `/etc/swx/swx.env` — `SECRET_KEY` and the database
credentials — not just public data. Back it up and restrict access to it
the same way you would the VM itself.

## One-time setup

**On the backup host** (as the user that will run the pull — a dedicated
service account is fine):
```
ssh-keygen -t ed25519 -f ~/.ssh/swx-backup-pull -N "" -C "swx-backup-pull@$(hostname)"
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

From the backup host:
```
bash deploy/backup-pull.sh
```
should copy the dump/config files and nothing else. Two checks worth doing
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

A simple daily cron entry on the backup host is enough:
```
30 4 * * *  /path/to/clima-espacial-deploy-copy/backup-pull.sh
```
(or a systemd user timer, same idea as `swx-dump.timer` on helio).

## Restore drill

Once per semester (docs/ARCHITECTURE.md §8): on a scratch machine,
`pg_restore` the latest pulled `.dump.gz` into a fresh database and confirm
`manage.py check` passes against it.
