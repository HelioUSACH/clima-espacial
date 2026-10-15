#!/usr/bin/env bash
# Runs on the OFF-MACHINE backup host (not on helio — D-039: the VM cannot
# reach that host on :22, so backups are pulled, never pushed). Install as a
# cron job or systemd user timer on the backup host; see README-backup.md
# for the one-time restricted-key setup this depends on.
set -euo pipefail

REMOTE="${SWX_BACKUP_REMOTE:-swx-backup-pull}"   # ~/.ssh/config Host alias
DEST="${SWX_BACKUP_DEST:-$HOME/backups/clima-espacial-usach}"

mkdir -p "$DEST"
# `rrsync -ro /srv/swx/backups/` (the forced command in helio's
# authorized_keys, README-backup.md) already restricts and anchors the
# remote path — rrsync treats whatever path the client sends as *relative
# to that restricted root*, so a bare "host:" (no path at all) is required.
# Repeating the path here (e.g. "$REMOTE:/srv/swx/backups/") makes rrsync
# concatenate the two and fail with "change_dir .../srv/swx/backups/srv/swx/backups
# failed: No such file or directory" — confirmed against a real rrsync.
rsync -az --delete "$REMOTE:" "$DEST/"
echo "Pulled backups into $DEST"
