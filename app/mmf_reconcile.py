"""Explicit local inventory import, using archive names/sizes, not content hashes."""
import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time

from app.mmf_manager import month_for


def inventory(root):
    entries = []
    archiver = shutil.which('7zz') or shutil.which('7z')
    if not archiver:
        raise ValueError('7-Zip is required to inspect existing archives.')
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.') and not Path(directory, d).is_symlink()]
        for name in files:
            path = Path(directory, name)
            if path.is_symlink() or not re.search(r'\.(zip|rar|7z)(\.001)?$', name, re.I):
                continue
            relative = path.relative_to(root)
            def add(member, size):
                entries.append({'filename': member.replace('\\', '/').split('/')[-1].split('^^')[-1],
                                'size': int(size), 'path': str(path), 'member': member,
                                'context': relative.parts[0]})
            add(name, path.stat().st_size)
            if not re.search(r'\.(7z|rar)(\.001)?$', name, re.I):
                continue
            result = subprocess.run([archiver, 'l', '-slt', '-p', '--', str(path)],
                                    capture_output=True, text=True, timeout=30)
            if result.returncode or '----------\n' not in result.stdout:
                raise ValueError('Cannot inventory archive: ' + str(relative))
            for block in result.stdout.split('----------\n', 1)[1].split('\n\n'):
                fields = dict(line.split(' = ', 1) for line in block.splitlines() if ' = ' in line)
                member = fields.get('Path', '')
                if re.search(r'\.(zip|rar|7z)$', member, re.I) and fields.get('Size', '').isdigit():
                    add(member, fields['Size'])
    return entries


def same_release(item, entry):
    label = item.get('release') or item.get('object_name', '')
    context = entry['context']
    # Never confuse a loyalty collection with a monthly standard release.
    special = re.search(r'\b(welcome|loyalty|bonus)\b|\b\d+\s*(months?|years?)\b', label, re.I)
    if special:
        duration = re.search(r'\b(\d+)\s*(month|year)s?\b', label, re.I)
        if duration:
            return bool(re.search(r'\b' + duration[1] + r'\s*' + duration[2] + r's?\b', context, re.I))
        return label.casefold() == context.casefold()
    if re.search(r'\b(loyalty|welcome|bonus)\b', context, re.I):
        return False
    month = month_for('', label)
    return bool(month and month == month_for('', context))


def match(item, entries):
    return next((e for e in entries if e['filename'] == item['filename']
                 and e['size'] == item['size'] and same_release(item, e)), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artist', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = root / 'data/mmf'
    with (directory / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = sqlite3.connect(f'file:{directory / "manager.sqlite3"}?mode=ro', uri=True)
        items = json.loads(db.execute("SELECT value FROM state WHERE key='items'").fetchone()[0])
        items = [i for i in items if i['creator'] == args.artist]
        if not items or len({i['folder'] for i in items}) != 1:
            raise ValueError('Choose one scanned artist with one destination folder.')
        base = Path(json.loads((root / 'data/settings.json').read_text())['download_directory'])
        artist = base / items[0]['folder']
        if artist.parent != base or artist.is_symlink() or not artist.is_dir():
            raise ValueError('Artist directory unavailable.')
        entries = inventory(artist)
        known = {k for k, in db.execute('SELECT key FROM completed')}
        records = []
        for item in items:
            evidence = match(item, entries)
            if evidence and item['key'] not in known:
                records.append({**item, 'destination': evidence['path'], 'completed_at': time.time(),
                                'reconciliation': {'method': 'release_filename_size', 'evidence': evidence,
                                                   'content_hash_verified': False}})
        db.close()
        report = {'artist': args.artist, 'scanned_files': len(items), 'new_matches': len(records),
                  'applied': args.apply, 'records': records}
        if args.apply:
            receipt = directory / ('reconciliation-' + str(time.time_ns()) + '.json')
            receipt.write_text(json.dumps(report, indent=2))
            receipt.chmod(0o600)
            with sqlite3.connect(directory / 'manager.sqlite3') as db:
                db.execute('BEGIN IMMEDIATE')
                for record in records:
                    db.execute('INSERT OR IGNORE INTO completed VALUES (?,?)', (record['key'], json.dumps(record)))
            print('Private reconciliation receipt:', receipt)
        print(json.dumps({k:v for k,v in report.items() if k != 'records'}))
        print(json.dumps(dict(Counter(r['release'] for r in records)), indent=2))


if __name__ == '__main__':
    main()
