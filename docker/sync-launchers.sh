#!/bin/bash
# Released pre-rename updaters only copy the legacy entry points and support/.
stl_package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
for stl_launcher in "$stl_package"/support/launchers/*; do
    [[ -f "$stl_launcher" ]] || continue
    stl_target="$stl_package/$(basename -- "$stl_launcher")"
    if [[ ! -e "$stl_target" ]]; then cp -- "$stl_launcher" "$stl_target";chmod u+x "$stl_target";fi
done
