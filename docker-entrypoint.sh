#!/bin/sh
# Start missingarr without root (C9).
#
# The container starts as root only long enough to hand the data directory to
# PUID:PGID. Existing installations have a root-owned ./data from earlier
# versions; this keeps them working without a manual chown.
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
DATA_DIR="${DATA_DIR:-/data}"

if [ "$(id -u)" != "0" ]; then
    # Started with --user: nothing to hand over.
    exec "$@"
fi

for id in "$PUID" "$PGID"; do
    case "$id" in
        ''|*[!0-9]*)
            echo "docker-entrypoint: PUID and PGID must be numbers" >&2
            exit 64
            ;;
    esac
done

if [ "$PUID" = "0" ]; then
    echo "docker-entrypoint: PUID=0 — running as root" >&2
    exec "$@"
fi

mkdir -p "$DATA_DIR"
# Only what does not already fit, so a normal restart touches nothing.
# Symlinks are skipped: chown and chmod would act on their target, which may
# lie outside the data directory.
find "$DATA_DIR" ! -type l \( ! -user "$PUID" -o ! -group "$PGID" \) -exec chown -h "$PUID:$PGID" {} +
# Private to the app user. The database holds the API keys and, without
# SECRET_KEY, the key to decrypt them — and so does every copy of it lying
# next to it (missingarr.db.bak-…).
find "$DATA_DIR" ! -type l -perm /077 -exec chmod go-rwx {} +
umask 077

exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups -- "$@"
