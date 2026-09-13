#!/usr/bin/env python3
"""Package only reviewed public files; keep all local source/catalog state private."""
import argparse
import json
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parent.parent
OMIT = {'.git', '__pycache__', 'node_modules', '.pytest_cache'}


def public_files(root):
    root = Path(root)
    manifest = json.loads((root / 'distribution.json').read_text())
    paths = set()
    for name in manifest['files'] + manifest['trees']:
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe distribution path.')
        base = root / relative
        if not base.exists():
            raise ValueError('Missing public source: ' + name)
        for path in base.rglob('*') if base.is_dir() else [base]:
            rel = path.relative_to(root)
            if any(part in OMIT for part in rel.parts):
                continue
            if path.is_symlink():
                raise ValueError('Distribution symlink requires review: ' + str(rel))
            if path.is_file():
                paths.add(rel)
    return sorted(paths)


def private_markers(root):
    """Read only the local source identity, never auth/session files."""
    markers = set()
    path = Path(root) / 'data/source.json'
    if path.exists():
        data = json.loads(path.read_text())
        if type(data.get('chat_id')) is int:
            markers.add(str(data['chat_id']))
    path = Path(root) / 'data/creators.json'
    if path.exists():
        source = json.loads(path.read_text()).get('source', {})
        url = source.get('url', '')
        match = re.search(r't\.me/c/([1-9]\d*)/', url)
        if match:
            markers.add(match[1])
            name = source.get('name', '').split('/')[0].strip()
            if len(name) >= 8:
                markers.add(name)
    return markers


def verify(root, paths, markers, read_content=None):
    normalized = {re.sub(r'[^a-z0-9]', '', value.casefold()) for value in markers}
    for rel in paths:
        if rel.parts[0] in {'data', 'dist'} or (len(rel.parts) == 1 and rel.name in {'catalog.html', 'configuration.html', 'organizer.html', 'collages.html'}):
            raise ValueError('Private/generated content in distribution: ' + str(rel))
        content = (read_content(rel) if read_content else (Path(root) / rel).read_bytes()).decode('utf-8', errors='ignore')
        compact = re.sub(r'[^a-z0-9]', '', content.casefold())
        if any(marker and marker in compact for marker in normalized):
            raise ValueError('Private source reference found in public file: ' + str(rel))


def package(root, output, paths):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation: an older release is never silently overwritten.
    with output.open('xb') as stream:
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for rel in paths:
                info = archive.gettarinfo(str(Path(root) / rel), arcname='telegram-stl/' + rel.as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                with (Path(root) / rel).open('rb') as content:
                    archive.addfile(info, content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Validate public files without creating an archive')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/telegram-stl-source.tar.gz')
    args = parser.parse_args()
    paths = public_files(ROOT)
    verify(ROOT, paths, private_markers(ROOT))
    print(f'Public source check passed: {len(paths)} files; private data and generated pages excluded.')
    if not args.check:
        package(ROOT, args.output, paths)
        print('Created', args.output)


if __name__ == '__main__':
    main()
