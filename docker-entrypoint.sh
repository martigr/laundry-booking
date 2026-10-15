#!/bin/sh
# Started as root: make the data folder belong to PUID:PGID (default 1000:1000),
# then drop privileges and run the app as that user. This way bind-mounted host
# folders work for any owner; set PUID/PGID to your host user to keep the files yours.
# Started with --user: run as that user unchanged (the folder must already be writable).
set -e

DATA_DIR="$(dirname "${DATABASE:-/data/laundry.db}")"

if [ "$(id -u)" = "0" ]; then
    PUID="${PUID:-1000}"
    PGID="${PGID:-1000}"
    mkdir -p "$DATA_DIR"
    # only the folder and the database files, never a whole host directory tree
    chown "$PUID:$PGID" "$DATA_DIR"
    find "$DATA_DIR" -maxdepth 1 -name 'laundry.db*' -exec chown "$PUID:$PGID" {} +
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups -- "$@"
fi

exec "$@"
