#!/usr/bin/env bash
# Runs on the OFF-MACHINE backup host (not on helio — D-039: the VM cannot
# reach that host on :22, so backups are pulled, never pushed). Install as a
# cron job or systemd user timer on the backup host; see README-backup.md
# for the one-time restricted-key setup this depends on.
set -euo pipefail

REMOTE="${SWX_BACKUP_REMOTE:-swx-backup-pull}"   # ~/.ssh/config Host alias
DEST="${SWX_BACKUP_DEST:-$HOME/backups/clima-espacial-usach}"
# Local retention, independent of helio's 14 days (D-070): the off-machine
# copy keeps its own, longer history.
KEEP_DAYS="${SWX_BACKUP_KEEP_DAYS:-90}"
[[ $KEEP_DAYS =~ ^[0-9]+$ ]] || { echo "SWX_BACKUP_KEEP_DAYS must be a whole number of days, got '$KEEP_DAYS'." >&2; exit 1; }

mkdir -p "$DEST"
# `rrsync -ro /srv/swx/backups/` (the forced command in helio's
# authorized_keys, README-backup.md) already restricts and anchors the
# remote path — rrsync treats whatever path the client sends as *relative
# to that restricted root*, so a bare "host:" (no path at all) is required.
# Repeating the path here (e.g. "$REMOTE:/srv/swx/backups/") makes rrsync
# concatenate the two and fail with "change_dir .../srv/swx/backups/srv/swx/backups
# failed: No such file or directory" — confirmed against a real rrsync.
#
# No --delete (D-070): helio prunes its own copies after 14 days, and with
# --delete the off-machine copy would lose them the same night, leaving no
# older history anywhere. Retention here is the prune below instead.
rsync -az --exclude='*.tmp' "$REMOTE:" "$DEST/"   # never a dump caught mid-write
echo "Pulled backups into $DEST"

# Prune only after a successful pull (set -e: a failed rsync exits above),
# so a broken link to helio never eats into the history kept here. -az
# keeps helio's mtimes, so this is the backup's age, not the pull's.
find "$DEST" -maxdepth 1 -type f -name 'swx-*' -mtime "+$KEEP_DAYS" -delete
echo "Pruned local backups older than $KEEP_DAYS days"
