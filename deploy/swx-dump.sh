#!/usr/bin/env bash
# Nightly local backup: pg_dump + the config bundle, kept on the VM for an
# off-machine host to *pull* (D-039: the VM cannot reach the backup host on
# :22, so nothing here ever pushes outward). Run as root by swx-dump.service.
#
# Ownership (D-051/P2): group-readable by `swx-backup` so the restricted
# pull key (README-backup.md) can actually read these files — root:root 700
# would have silently made every backup unreachable. Falls back to root:root
# if that group doesn't exist yet (first run, before README-backup.md's
# setup), so the dump itself never fails for a provisioning-order reason.
set -euo pipefail

BACKUP_DIR=/srv/swx/backups
KEEP_DAYS=14
STAMP="$(date +%Y%m%d-%H%M)"

if getent group swx-backup >/dev/null; then
    DIR_OWNER="root:swx-backup"
    DIR_MODE=750
    FILE_MODE=640
else
    echo "WARNING: group 'swx-backup' doesn't exist yet (see deploy/README-backup.md)." \
         "Backups will be written root:root 700 — unreadable by the pull host until that's set up." >&2
    DIR_OWNER="root:root"
    DIR_MODE=700
    FILE_MODE=600
fi
install -d -o root -g "${DIR_OWNER#*:}" -m "$DIR_MODE" "$BACKUP_DIR"

runuser -u postgres -- pg_dump --format=custom swx \
    | gzip > "$BACKUP_DIR/swx-$STAMP.dump.gz.tmp"
mv "$BACKUP_DIR/swx-$STAMP.dump.gz.tmp" "$BACKUP_DIR/swx-$STAMP.dump.gz"

# Config bundle: the env file (secrets) and every manifest, so a restore
# doesn't depend on remembering what was deployed. It contains SECRET_KEY
# and the database credentials — the backup host must be treated as a
# sensitive asset, not just "a copy of public data" (README-backup.md).
#
# Analytics too (22-HANDOFF-v0.3.0.md §2.1): GoatCounter's SQLite database
# and its import token. The database is copied with SQLite's online backup
# API (Python's sqlite3.Connection.backup, the same API as the CLI's
# `.backup`, without needing the sqlite3 package on the VM), so the copy is
# consistent while goatcounter keeps writing. Either piece missing (e.g.
# analytics not set up yet) only warns: the database dump above matters more.
GC_DB=/srv/swx/goatcounter/db.sqlite3
GC_ENV=/etc/swx/goatcounter-import.env
# Staged outside BACKUP_DIR: a pull overlapping the dump must never meet
# a root-only directory it cannot read.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
EXTRA=()
if [[ -f $GC_DB ]]; then
    python3 -c 'import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
with dst: src.backup(dst)
dst.close(); src.close()' "$GC_DB" "$STAGE/goatcounter-db.sqlite3"
    EXTRA+=(-C "$STAGE" goatcounter-db.sqlite3)
else
    echo "WARNING: $GC_DB not found — analytics not in this backup." >&2
fi
if [[ -f $GC_ENV ]]; then
    EXTRA+=(-C / "${GC_ENV#/}")
else
    echo "WARNING: $GC_ENV not found — analytics token not in this backup." >&2
fi

tar --create --gzip \
    --file "$BACKUP_DIR/swx-config-$STAMP.tar.gz" \
    -C / etc/swx/swx.env \
    -C /opt/swx modules \
    "${EXTRA[@]}"

chown "$DIR_OWNER" "$BACKUP_DIR"/swx-config-"$STAMP".tar.gz "$BACKUP_DIR"/swx-"$STAMP".dump.gz
chmod "$FILE_MODE" "$BACKUP_DIR"/swx-config-"$STAMP".tar.gz "$BACKUP_DIR"/swx-"$STAMP".dump.gz

# Top level only: nrt/ (swx-export-nrt.service, D-091) prunes itself.
find "$BACKUP_DIR" -maxdepth 1 -name 'swx-*.dump.gz' -mtime "+$KEEP_DAYS" -delete
find "$BACKUP_DIR" -maxdepth 1 -name 'swx-config-*.tar.gz' -mtime "+$KEEP_DAYS" -delete

echo "Backup done: $BACKUP_DIR/swx-$STAMP.dump.gz"
