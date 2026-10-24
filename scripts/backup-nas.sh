#!/bin/sh
# Example: back up the database to a NAS (NFS mount), run daily by a systemd timer or cron
# (see README.md, "Backup and restore"). Uses "flask backup" in a one-off container, so the copy
# is consistent while the app is running. Backups older than KEEP_DAYS are deleted.
#
#   BACKUP_DIR=/mnt/nas/laundry-booking  target directory (must be a mount point)
#   KEEP_DAYS=30                         age in days after which backups are deleted
#   REQUIRE_MOUNT=1                      0 allows a plain local directory (testing)
set -eu

BACKUP_DIR="${BACKUP_DIR:-/mnt/nas/laundry-booking}"
KEEP_DAYS="${KEEP_DAYS:-30}"
REQUIRE_MOUNT="${REQUIRE_MOUNT:-1}"

cd "$(dirname "$0")/.."  # where docker-compose.yml is

# Never write into the empty local mount directory when the NAS is not mounted
# (accessing it first triggers a systemd automount)
ls "$BACKUP_DIR" >/dev/null
if [ "$REQUIRE_MOUNT" = 1 ] && ! mountpoint -q "$BACKUP_DIR"; then
    echo "error: $BACKUP_DIR is not mounted" >&2
    exit 1
fi

# Write under a temporary name and rename on success: an aborted run never leaves a broken
# file that looks like the newest backup (and the cleanup below does not match it)
name="laundry-$(date +%Y-%m-%d_%H%M%S).db"
tmp=".$name.part"
trap 'rm -f "$BACKUP_DIR/$tmp"' EXIT
trap 'exit 1' INT TERM

# umask 077: backups contain password hashes and mail addresses (a chmod from the host can
# fail on NFS with root_squash, the files belong to the app user)
docker compose run --rm --no-deps -T -v "$BACKUP_DIR:/backup" notifier \
    sh -c 'umask 077 && exec flask --app laundry backup "/backup/$1"' sh "$tmp"
mv "$BACKUP_DIR/$tmp" "$BACKUP_DIR/$name"

# Only after a successful backup, so failing runs never delete the last good copies
find "$BACKUP_DIR" -maxdepth 1 -name 'laundry-*.db' -mtime +"$KEEP_DAYS" -delete
echo "backup written: $BACKUP_DIR/$name"
