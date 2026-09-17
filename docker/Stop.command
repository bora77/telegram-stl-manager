#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:$HOME/.docker/bin"
# Compose needs the saved path even for commands which do not mount it.
IFS= read -r STL_DOWNLOAD_DIRECTORY < support/download-folder.txt
export STL_DOWNLOAD_DIRECTORY
docker compose stop
echo 'Stopped. Account data, history and downloaded files are retained.'
