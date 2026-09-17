#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:$HOME/.docker/bin"
trap 'echo "Startup failed. Read the error above."; read -r -p "Press Return to close… " || true' ERR
command -v docker >/dev/null || { echo 'Install and start Docker Desktop (Mac), or Docker Engine with Compose (Linux), first.'; false; }
docker info >/dev/null
docker compose version
if [[ -n "${1:-}" ]]; then
    stl_folder=$1
elif [[ -f support/download-folder.txt ]]; then
    IFS= read -r stl_folder < support/download-folder.txt
elif [[ $(uname -s) == Darwin ]]; then
    stl_folder=$(osascript -e 'POSIX path of (choose folder with prompt "Choose the folder where Telegram STL Manager should save releases")')
else
    stl_folder="$HOME/Downloads/Telegram STL Manager"
    mkdir -p -- "$stl_folder"
fi
[[ -d "$stl_folder" && -w "$stl_folder" ]] || { echo 'Download folder is missing or not writable. Mount it first, or pass another folder to Start.command.'; false; }
export STL_DOWNLOAD_DIRECTORY=$(cd -- "$stl_folder" && pwd -P)
export STL_UID=$(id -u) STL_GID=$(id -g)
printf '%s\n' "$STL_DOWNLOAD_DIRECTORY" > support/download-folder.txt
echo "Downloads on your computer: $STL_DOWNLOAD_DIRECTORY"
echo 'In app Configuration, use /downloads as the download location.'
echo 'Building and starting. The first build downloads dependencies and may take several minutes.'
docker compose --progress plain up --build -d --wait --wait-timeout 120
if [[ $(uname -s) == Darwin ]]; then
    open http://localhost:6093
elif command -v xdg-open >/dev/null; then
    xdg-open http://localhost:6093 || true
fi
echo 'Open http://localhost:6093. Ctrl+C closes this log view; Stop.command stops the app.'
trap - ERR
docker compose logs --follow --tail 80
