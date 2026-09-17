#!/bin/bash
set -euo pipefail
cd /home/stl/telegram-stl
export PATH="$PWD/runtime/python/bin:$PATH"
mkdir -p data
exec 9>data/windows-launch.lock
flock -n 9 || exit 0
printf '{"action":"restore"}\n' | sudo /usr/bin/python3 /usr/local/lib/telegram-stl/share-helper.py || true
exec bash start.sh
