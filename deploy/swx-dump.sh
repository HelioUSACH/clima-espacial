#!/usr/bin/env bash
# Nightly local backup: pg_dump + the config bundle, kept on the VM for an
# off-machine host to *pull* (D-039: the VM cannot reach the backup host on
# :22, so nothing here ever pushes outward). Run as root by swx-dump.service.
set -euo pipefail

BACKUP_DIR=/srv/swx/backups
KEEP_DAYS=14
STAMP="$(date +%Y%m%d-%H%M)"

install -d -o root -g root -m 700 "$BACKUP_DIR"

runuser -u postgres -- pg_dump --format=custom swx \
    | gzip > "$BACKUP_DIR/swx-$STAMP.dump.gz.tmp"
mv "$BACKUP_DIR/swx-$STAMP.dump.gz.tmp" "$BACKUP_DIR/swx-$STAMP.dump.gz"

# Config bundle: the env file (secrets) and every manifest, so a restore
# doesn't depend on remembering what was deployed. 0600: only the backup
# host's forced-command key (root-equivalent for this one path) can read it.
tar --create --gzip \
    --file "$BACKUP_DIR/swx-config-$STAMP.tar.gz" \
    -C / etc/swx/swx.env \
    -C /opt/swx modules
chmod 600 "$BACKUP_DIR"/swx-config-"$STAMP".tar.gz "$BACKUP_DIR"/swx-"$STAMP".dump.gz

find "$BACKUP_DIR" -name 'swx-*.dump.gz' -mtime "+$KEEP_DAYS" -delete
find "$BACKUP_DIR" -name 'swx-config-*.tar.gz' -mtime "+$KEEP_DAYS" -delete

echo "Backup done: $BACKUP_DIR/swx-$STAMP.dump.gz"
