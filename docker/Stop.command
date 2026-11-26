#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
bash support/sync-launchers.sh
export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:$HOME/.docker/bin"
# Compose needs the saved path even for commands which do not mount it.
IFS= read -r STL_DOWNLOAD_DIRECTORY < support/download-folder.txt
export STL_DOWNLOAD_DIRECTORY
if [[ -f support/data-volume.txt ]]; then IFS= read -r STL_DATA_VOLUME < support/data-volume.txt;export STL_DATA_VOLUME;fi
docker compose stop
echo 'Stopped. Account data, history and downloaded files are retained.'
