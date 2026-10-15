#!/usr/bin/env bash
# Runs on the OFF-MACHINE backup host (not on helio — D-039: the VM cannot
# reach that host on :22, so backups are pulled, never pushed). Install as a
# cron job or systemd user timer on the backup host; see README-backup.md
# for the one-time restricted-key setup this depends on.
set -euo pipefail

REMOTE="${SWX_BACKUP_REMOTE:-swx-backup-pull}"   # ~/.ssh/config Host alias
DEST="${SWX_BACKUP_DEST:-$HOME/backups/clima-espacial-usach}"

mkdir -p "$DEST"
# The remote command is fixed by the forced `rrsync -ro` entry in helio's
# authorized_keys (README-backup.md) — this path argument is only a label.
rsync -az --delete "$REMOTE:/srv/swx/backups/" "$DEST/"
echo "Pulled backups into $DEST"
