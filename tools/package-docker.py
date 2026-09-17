#!/usr/bin/env python3
"""Build a public Docker source bundle, never copying a private working tree."""
import hashlib
import importlib.util
from pathlib import Path
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent

def main():
    spec = importlib.util.spec_from_file_location('public', ROOT/'tools/package-release.py')
    public = importlib.util.module_from_spec(spec); spec.loader.exec_module(public)
    files = public.public_files(ROOT)
    public.verify(ROOT, files, public.private_markers(ROOT))
    output = ROOT/'dist/Telegram-STL-Manager-Docker.zip'
    output.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        package = Path(tmp)/'Telegram-STL-Manager-Docker'
        support = package/'support'; support.mkdir(parents=True)
        for rel in files:
            target = support/'app'/rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT/rel, target)
        for name in ('Dockerfile', 'entrypoint.sh', '.dockerignore'):
            shutil.copy2(ROOT/'docker'/name, support/name)
        for name in ('Start.command', 'Stop.command', 'compose.yaml', 'README.md'):
            shutil.copy2(ROOT/'docker'/name, package/name)
        with zipfile.ZipFile(output.with_suffix('.tmp'), 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package.rglob('*')):
                if path.is_file(): archive.write(path, path.relative_to(tmp))
        output.with_suffix('.tmp').replace(output)
    output.with_suffix('.zip.sha256').write_text(hashlib.sha256(output.read_bytes()).hexdigest()+'  '+output.name+'\n')
    print(output)

if __name__ == '__main__': main()
