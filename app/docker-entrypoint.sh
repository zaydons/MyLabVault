#!/bin/sh
# Start MyLabVault as an unprivileged user.
#
# Started as root (the default), this makes sure the data folder belongs to the app user and then
# drops privileges. PUID/PGID choose that user's IDs (default 1001), for example 568 to match the
# TrueNAS "apps" user. Started with --user/user: already, it runs as given.
set -e

DATA_DIR="${MYLABVAULT_DATA_DIR:-/app/data}"

if [ "$(id -u)" = "0" ]; then
    PUID="${PUID:-1001}"
    PGID="${PGID:-1001}"
    mkdir -p "$DATA_DIR/uploads/pdfs"
    # Only walk the folder when something isn't owned by the app user yet (first start after
    # upgrading from an image that ran as root, or a changed PUID)
    if [ -n "$(find "$DATA_DIR" \( ! -user "$PUID" -o ! -group "$PGID" \) -print -quit)" ]; then
        echo "Setting ownership of $DATA_DIR to $PUID:$PGID"
        chown -R "$PUID:$PGID" "$DATA_DIR"
    fi
    exec su-exec "$PUID:$PGID" "$@"
fi

exec "$@"
