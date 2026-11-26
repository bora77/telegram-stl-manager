#!/bin/bash
set -euo pipefail
stl_package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ! -f "$stl_package/compose.yaml" ]]; then stl_package=$(cd -- "$stl_package/../.." && pwd);fi
exec bash "$stl_package/Update.command" "$@"
