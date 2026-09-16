#!/usr/bin/env bash
set -euo pipefail
stl_source="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
stl_destination="${1:-${XDG_DATA_HOME:-$HOME/.local/share}/telegram-stl-manager}"
if [[ -e "$stl_destination" ]]; then
  echo "Destination already exists: $stl_destination. Choose a new directory; existing user data will not be overwritten." >&2
  exit 1
fi
mkdir -p -- "$(dirname -- "$stl_destination")"
cp -a -- "$stl_source" "$stl_destination"
printf 'Installed. Start with: %q/start.sh\nNo services were installed.\n' "$stl_destination"
