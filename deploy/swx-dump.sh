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
tar --create --gzip \
    --file "$BACKUP_DIR/swx-config-$STAMP.tar.gz" \
    -C / etc/swx/swx.env \
    -C /opt/swx modules

chown "$DIR_OWNER" "$BACKUP_DIR"/swx-config-"$STAMP".tar.gz "$BACKUP_DIR"/swx-"$STAMP".dump.gz
chmod "$FILE_MODE" "$BACKUP_DIR"/swx-config-"$STAMP".tar.gz "$BACKUP_DIR"/swx-"$STAMP".dump.gz

find "$BACKUP_DIR" -name 'swx-*.dump.gz' -mtime "+$KEEP_DAYS" -delete
find "$BACKUP_DIR" -name 'swx-config-*.tar.gz' -mtime "+$KEEP_DAYS" -delete

echo "Backup done: $BACKUP_DIR/swx-$STAMP.dump.gz"
