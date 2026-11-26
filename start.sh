#!/usr/bin/env bash
set -euo pipefail
stl_app="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$stl_app"
if [[ ! -x runtime/python/bin/python3 ]]; then
  echo 'This is a source checkout. Use the Windows installer or Docker package, or follow source setup in INSTALL.md.' >&2
  exit 1
fi
export PYTHONNOUSERSITE=1
exec runtime/python/bin/python3 tools/launch.py
