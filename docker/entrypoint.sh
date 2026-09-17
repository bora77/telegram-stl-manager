#!/bin/sh
set -eu
stl_uid=${STL_UID:-1000}
stl_gid=${STL_GID:-1000}
case "$stl_uid:$stl_gid" in *[!0-9:]*|:*|*:) echo 'Invalid numeric user/group ID.' >&2; exit 1;; esac
if [ "$stl_uid" -eq 0 ]; then echo 'Use a non-root host user to start the app.' >&2; exit 1; fi
umask 077
export USER=stl LOGNAME=stl
mkdir -p /state/data /state/home
# Only application-owned state and code are changed; never chown the download mount.
chown "$stl_uid:$stl_gid" /state /state/data /state/home
# The code is read-only to the worker; only the top-level directory needs to
# allow generated HTML/cache files. Avoid copying every source file into the
# container's writable layer on first start.
chown "$stl_uid:$stl_gid" /opt/app
exec setpriv --reuid="$stl_uid" --regid="$stl_gid" --clear-groups \
    python tools/launch.py
