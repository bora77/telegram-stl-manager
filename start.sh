#!/usr/bin/env bash
set -euo pipefail
stl_app="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$stl_app"
if [[ ! -x runtime/python/bin/python3 ]]; then
  echo 'This is the source package. Download the Linux x64 runtime package, or follow INSTALL.md.' >&2
  exit 1
fi
export PYTHONNOUSERSITE=1
exec runtime/python/bin/python3 tools/launch.py
