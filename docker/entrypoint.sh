#!/bin/sh
# Start mtdl-web as PUID:PGID with UMASK, under tini. Started as root, the script only hands /config over and drops
# its rights; tini comes after that, so no process keeps root once mtdl-web runs. exec keeps PID 1: tini forwards
# docker stop to the app and reaps what hook scripts leave behind. Started as another user (user: in Compose) there
# is no root step at all; /config must belong to that user then. Downloads and other user folders are never chowned
# (the write test reports a wrong owner instead).
set -eu

PUID="${PUID:-99}"
PGID="${PGID:-100}"
UMASK="${UMASK:-022}"
CONFIG="${MTDL_CONFIG_DIR:-/config}"
CACHE="${MTDL_CACHE_DIR:-$CONFIG/cache}"

umask "$UMASK"
if [ "$(id -u)" = 0 ]; then
    mkdir -p "$CONFIG" "$CACHE"
    # Only the top levels: a fresh bind mount comes up as root. Files inside belong to the app already.
    chown "$PUID:$PGID" "$CONFIG" "$CACHE"
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups --inh-caps=-all -- tini -- "$@"
fi
exec tini -- "$@"
