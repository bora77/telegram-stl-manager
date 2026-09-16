#!/usr/bin/env python3
"""Create blank private settings without replacing an existing installation."""
import os
import json
from pathlib import Path


def initialize(root):
    root = Path(root)
    (root / 'data').mkdir(exist_ok=True, mode=0o700)
    settings=root/'data/settings.json'
    if not settings.exists():
        downloads=root/'downloads';downloads.mkdir(exist_ok=True)
        try:
            with settings.open('x') as stream:json.dump({'revision':0,'download_directory':str(downloads.resolve())},stream,indent=2)
        except FileExistsError:pass
    for name in ('source', 'creators', 'incoming-folders'):
        target = root / 'data' / (name + '.json')
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            print('Kept existing', target.name)
            continue
        with os.fdopen(fd, 'wb') as stream:
            stream.write((root / 'examples' / (name + '.example.json')).read_bytes())
        print('Created blank', target.name)


if __name__ == '__main__':
    initialize(Path(__file__).resolve().parent.parent)
