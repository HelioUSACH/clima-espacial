# Off-machine backups (D-039)

helio cannot reach the backup host (donnager, icaria as fallback) on port
22 (VM preflight, 2026-10-15) — so the backup host **pulls**, over a key
that can only read `/srv/swx/backups/` and nothing else on helio. Nightly
content of that directory is produced by `swx-dump.service`/`.timer`
(`deploy/swx-dump.sh`): a `pg_dump` + a config bundle, 14-day local retention.

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
useradd --system --shell /usr/sbin/nologin --home-dir /srv/swx/backups swx-backup
usermod -aG swx swx-backup   # swx-backup.sh already writes that directory group-readable? no — see below
chmod 750 /srv/swx/backups   # readable by the swx-backup group only
chgrp swx-backup /srv/swx/backups
mkdir -p /home/swx-backup/.ssh
gunzip -c /usr/share/doc/rsync/scripts/rrsync.gz > /usr/local/bin/rrsync
chmod 755 /usr/local/bin/rrsync
```
Then add the backup host's **public** key to
`/home/swx-backup/.ssh/authorized_keys`, forced to read-only `rrsync` scoped
to the backup directory and nothing else:
```
command="/usr/local/bin/rrsync -ro /srv/swx/backups/",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA...the-backup-host's-public-key...
```

**Verify** (from the backup host): `bash deploy/backup-pull.sh` — should copy
the dump/config files and nothing else; `ssh swx-backup-pull` with no command
should be refused (the forced command is the only thing that key can ever
run).

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
