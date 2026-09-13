#!/usr/bin/env python3
"""Check the actual Git index against the public manifest before committing."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('release_packager', ROOT/'tools/package-release.py')
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


def git(root, *args, data=None):
    return subprocess.run(['git', '-C', str(root), *args], input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def staged_files(root):
    entries = {}
    for entry in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not entry:continue
        metadata, name = entry.split(b'\t', 1)
        mode, oid, stage = metadata.split()
        path = Path(name.decode('utf-8'))
        if stage != b'0':raise ValueError('Resolve staged conflicts before committing.')
        if mode not in (b'100644', b'100755'):
            raise ValueError('Only regular public source files may be committed: '+str(path))
        entries[path] = oid
    # Read blobs from the index, so unstaged edits cannot conceal staged content.
    objects = list(dict.fromkeys(entries.values()))
    output = git(root, 'cat-file', '--batch', data=b''.join(oid+b'\n' for oid in objects))
    blobs, offset = {}, 0
    for oid in objects:
        end = output.index(b'\n', offset)
        actual, kind, size = output[offset:end].split()
        if actual != oid or kind != b'blob':raise ValueError('Invalid staged source object.')
        size = int(size);offset = end+1
        blobs[oid] = output[offset:offset+size];offset += size
        if output[offset:offset+1] != b'\n':raise ValueError('Incomplete staged source object.')
        offset += 1
    return {path:blobs[oid] for path, oid in entries.items()}


def verify_index(root):
    root = Path(root)
    contents = staged_files(root)
    if Path('distribution.json') not in contents:raise ValueError('Stage the public distribution manifest first.')
    manifest = json.loads(contents[Path('distribution.json')])
    approved = {}
    for kind in ('files', 'trees'):
        values = manifest.get(kind)
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError('Invalid public distribution manifest.')
        paths = {Path(value) for value in values}
        if any(p.is_absolute() or '..' in p.parts or not p.parts for p in paths):
            raise ValueError('Unsafe public distribution path.')
        approved[kind] = paths
    for path in contents:
        if path not in approved['files'] and not any(tree in path.parents for tree in approved['trees']):
            raise ValueError('File is outside the public distribution manifest: '+str(path))
        if any(part in packager.OMIT for part in path.parts):
            raise ValueError('Private build or repository data is staged: '+str(path))
    missing = approved['files']-contents.keys()
    if missing:raise ValueError('Required public files are missing from the index: '+', '.join(str(p) for p in sorted(missing)))
    packager.verify(root, sorted(contents), packager.private_markers(root), read_content=contents.__getitem__)
    return len(contents)


def main():
    try:count = verify_index(ROOT)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print('Git privacy check failed: '+str(error), file=sys.stderr)
        return 1
    print(f'Git privacy check passed: {count} staged public files; private state excluded.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
