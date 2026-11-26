#!/bin/bash
# Docker stays on the host. The application never receives its socket.
set -Eeuo pipefail
cd -- "$(dirname -- "$0")"
bash support/sync-launchers.sh
export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:$HOME/.docker/bin"
root=$PWD
launchers=(Install-Linux.sh Start-Linux.sh Stop-Linux.sh Update-Linux.sh Install-macOS.command Start-macOS.command Stop-macOS.command Update-macOS.command)
command -v docker >/dev/null || { echo 'Install/start Docker first.'; exit 1; }
docker info >/dev/null
[[ $(id -u) != 0 ]] || { echo 'Run as your normal Docker user, not root.'; exit 1; }
[[ -f support/download-folder.txt ]] || { echo 'Start the app once before updating.'; exit 1; }
IFS= read -r STL_DOWNLOAD_DIRECTORY < support/download-folder.txt
export STL_DOWNLOAD_DIRECTORY STL_UID=$(id -u) STL_GID=$(id -g)
[[ -d "$STL_DOWNLOAD_DIRECTORY" && -w "$STL_DOWNLOAD_DIRECTORY" ]] || { echo 'Reconnect your download folder before updating.'; exit 1; }
if [[ -f support/data-volume.txt ]]; then IFS= read -r STL_DATA_VOLUME < support/data-volume.txt; export STL_DATA_VOLUME; fi
mkdir -p .updates
if ! mkdir .updates/lock 2>/dev/null; then
    owner=$(cat .updates/lock/pid 2>/dev/null || true)
    if [[ "$owner" =~ ^[0-9]+$ ]] && kill -0 "$owner" 2>/dev/null; then echo 'Another updater is running.'; exit 1; fi
    [[ ${1:-} == --recover ]] || { echo 'Interrupted update detected. Run bash Update.command --recover.'; exit 1; }
    rm -f .updates/lock/pid
    rmdir .updates/lock
    mkdir .updates/lock
fi
printf '%s\n' "$$" > .updates/lock/pid
trap 'rm -f "$root/.updates/lock/pid"; rmdir "$root/.updates/lock" 2>/dev/null || true' EXIT
tx="$root/.updates/current"
helper(){ docker exec -i --user "$STL_UID:$STL_GID" "$container" python - "$@" < "$tx/helper.py"; }
compose(){ docker compose -f "$root/compose.yaml" -f "$tx/image.yaml" "$@"; }
restore_files(){
    failed_package=$(mktemp -d "$tx/failed-package.XXXXXX") || return 1
    for name in support Start.command Stop.command Update.command compose.yaml README.md "${launchers[@]}"; do
        if [[ -e "$tx/previous/$name" ]]; then
            if [[ -e "$root/$name" ]]; then mv "$root/$name" "$failed_package/$name" || return 1; fi
            mv "$tx/previous/$name" "$root/$name" || return 1
        fi
    done
}
rollback(){
    echo 'Restoring the previous application and original data volume…'
    restore_files || return 1
    IFS= read -r STL_DATA_VOLUME < "$tx/old-volume";export STL_DATA_VOLUME
    old_image=$(cat "$tx/old-image")
    printf 'services:\n  manager:\n    image: %s\n' "$old_image" > "$tx/image.yaml"
    compose up --no-build --force-recreate -d --wait --wait-timeout 120 || return 1
    container=$(compose ps -q manager)
    helper release "$token" || return 1
    touch "$tx/rolled-back"
    echo 'Rollback complete. Original settings, sessions, history and download folder retained.'
}
failed(){
    trap - ERR INT TERM
    set +e
    if [[ -f "$tx/stopped" ]]; then
        if ! rollback; then echo 'Recovery needs attention. Keep .updates/current and run Update.command --recover.'; exit 1; fi
    elif [[ -f "$tx/reserved" ]]; then helper release "$token"; fi
    echo 'Update did not complete. The transaction and any data copies were retained.'
    exit 1
}
if [[ ${1:-} == --recover ]]; then
    if [[ -d "$tx" && ! -f "$tx/stopped" && ! -f "$tx/reserved" ]]; then
        mv "$tx" "$root/.updates/recovered-preparation-$(date +%s)-$$"
        echo 'Recovered interrupted preparation; the running app was unchanged.'
        exit 0
    fi
    [[ -f "$tx/token" && -f "$tx/old-volume" ]] || { echo 'No recoverable transaction found.'; exit 1; }
    token=$(cat "$tx/token")
    for key in uid gid; do [[ $(cat "$tx/$key") =~ ^[0-9]+$ ]] || exit 1; done
    export STL_UID=$(cat "$tx/uid") STL_GID=$(cat "$tx/gid") STL_TIMEZONE=$(cat "$tx/timezone")
    container=$(docker compose ps -q manager)
    if [[ -f "$tx/stopped" && ! -f "$tx/rolled-back" ]]; then rollback; elif [[ -f "$tx/reserved" ]]; then helper release "$token"; fi
    mv "$tx" "$root/.updates/recovered-$token"
    exit 0
fi
[[ ! -e "$tx" ]] || { echo 'A previous transaction remains. Run Update.command --recover first.'; exit 1; }
container=$(docker compose ps -q manager)
[[ -n "$container" ]] || { echo 'Start the app before updating.'; exit 1; }
mkdir "$tx"
cp support/update-helper.py "$tx/helper.py"
token=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
printf '%s\n' "$token" > "$tx/token"
trap failed ERR INT TERM
old_image=$(docker inspect -f '{{.Image}}' "$container")
old_volume=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/state"}}{{.Name}}{{end}}{{end}}' "$container")
[[ "$old_image" =~ ^sha256:[a-f0-9]{64}$ && "$old_volume" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]+$ ]] || { echo 'Unexpected container/storage layout.'; false; }
docker tag "$old_image" "telegram-stl-manager-rollback:$token"
printf '%s\n' "telegram-stl-manager-rollback:$token" > "$tx/old-image";printf '%s\n' "$old_volume" > "$tx/old-volume"
for key in STL_UID STL_GID TZ; do
    value=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$container" | sed -n "s/^${key}=//p")
    case "$key" in STL_UID) export STL_UID=$value;; STL_GID) export STL_GID=$value;; TZ) export STL_TIMEZONE=$value;; esac
done
printf '%s\n' "$STL_UID" > "$tx/uid";printf '%s\n' "$STL_GID" > "$tx/gid";printf '%s\n' "$STL_TIMEZONE" > "$tx/timezone"
if [[ ${1:-} == --package ]]; then
    [[ $# == 4 && -f "$2" ]] || { echo 'Usage: Update.command --package ZIP SHA256 VERSION'; false; }
    docker exec --user "$STL_UID:$STL_GID" "$container" mkdir -p "/state/docker-updates/$token"
    docker cp "$2" "$container:/state/docker-updates/$token/package.zip"
    docker exec --user 0 "$container" chown "$STL_UID:$STL_GID" "/state/docker-updates/$token/package.zip"
    latest=$(helper prepare "$token" "$3" "$4")
else
    [[ $# == 0 ]] || { echo 'Unknown update option.'; false; }
    latest=$(helper prepare "$token")
fi
if [[ "$latest" == CURRENT ]]; then echo 'Already up to date.';mv "$tx" "$root/.updates/checked-$token";exit 0;fi
[[ "$latest" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Invalid release response.'; false; }
echo "Verified release $latest. Building before stopping the current app…"
mkdir "$tx/candidate"
docker cp "$container:/state/docker-updates/$token/package/." "$tx/candidate/"
helper cleanup "$token"
new_image="telegram-stl-manager-update:$token"
docker build --progress plain -t "$new_image" -f "$tx/candidate/support/Dockerfile" "$tx/candidate/support"
echo 'Waiting for active tasks. New work is temporarily blocked.'
reservation=$(helper reserve "$token")
touch "$tx/reserved"
while [[ "$reservation" == BUSY ]]; do sleep 2;reservation=$(helper reserve "$token");done
touch "$tx/stopped"
docker stop --time 60 "$container"
new_volume="telegram-stl-manager-data-$token"
docker volume create "$new_volume" >/dev/null
echo 'Copying application state to a new volume; the original is retained for rollback…'
docker run --rm --network none --user 0 --entrypoint /bin/sh -v "$old_volume:/source:ro" -v "$new_volume:/target" "$old_image" -c 'cp -a /source/. /target/'
mkdir "$tx/previous"
cp support/download-folder.txt "$tx/candidate/support/download-folder.txt"
if [[ -f support/installation-acknowledged ]]; then cp support/installation-acknowledged "$tx/candidate/support/installation-acknowledged";fi
printf '%s\n' "$new_volume" > "$tx/candidate/support/data-volume.txt"
for name in support Start.command Stop.command Update.command compose.yaml README.md "${launchers[@]}"; do
    if [[ -e "$root/$name" ]]; then mv "$root/$name" "$tx/previous/$name";fi
    mv "$tx/candidate/$name" "$root/$name"
done
export STL_DATA_VOLUME=$new_volume
printf 'services:\n  manager:\n    image: %s\n' "$new_image" > "$tx/image.yaml"
compose up --no-build --force-recreate -d --wait --wait-timeout 120
container=$(compose ps -q manager)
actual=$(docker exec "$container" cat /opt/app/VERSION)
[[ "$actual" == "$latest" ]] || { echo 'Started version differs from the verified release.'; false; }
helper release "$token"
printf '%s\n' "$new_image" > "$root/support/image.txt"
mv "$tx" "$root/.updates/completed-$token"
trap - ERR INT TERM
echo "Updated to $latest. Your previous data volume and package remain under .updates for recovery."
echo 'Open http://localhost:6093. No downloaded files were removed.'
