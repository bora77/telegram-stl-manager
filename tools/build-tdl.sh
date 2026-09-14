#!/usr/bin/env bash
set -euo pipefail
stl_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
stl_go="$stl_root/.tools/tdl-research/go/bin/go"
if [[ ! -x "$stl_go" ]]; then stl_go="$(command -v go)"; fi
export GOTOOLCHAIN=local GOMAXPROCS=2 GOMEMLIMIT=2GiB CGO_ENABLED=0
export GOCACHE="$stl_root/.tools/tdl-research/go-cache"
export GOMODCACHE="$stl_root/.tools/tdl-research/go-modules"
cd "$stl_root/.tools/tdl-stl/src"
nice -n 10 "$stl_go" build -p 1 -trimpath -o ../tdl.new .
mv ../tdl.new ../tdl
python3 - "$stl_root" <<'PY'
from pathlib import Path
import hashlib,json,sys
from datetime import datetime,timezone
root=Path(sys.argv[1]);directory=root/'.tools/tdl-stl'
data={'tdl_version':'v0.20.4','gotd_version':'v0.140.0','local_command':'stl',
      'service_protocol':1,
      'binary_sha256':hashlib.sha256((directory/'tdl').read_bytes()).hexdigest(),
      'built_at':datetime.now(timezone.utc).isoformat()}
(directory/'build.json').write_text(json.dumps(data,indent=2)+'\n')
print('Built standalone Telegram STL manager CLI:',directory/'tdl')
PY
