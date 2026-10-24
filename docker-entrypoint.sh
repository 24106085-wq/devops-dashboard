#!/bin/sh
# Start the app as the unprivileged "app" user.
# When started as root, first make the data directory writable for that user
# (volumes created by earlier root-run versions of the image are root-owned).
# Only ownership changes; the database contents are never modified or removed.
set -eu

if [ "$(id -u)" = "0" ]; then
    data_dir="$(dirname "${DB_PATH:-/data/dashboard.db}")"
    if [ "$data_dir" != "/" ] && [ "$data_dir" != "." ]; then
        mkdir -p "$data_dir"
        chown -R app:app "$data_dir"
    fi
    exec setpriv --reuid=app --regid=app --init-groups "$@"
fi

exec "$@"
