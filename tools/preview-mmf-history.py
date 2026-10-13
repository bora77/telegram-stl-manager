#!/usr/bin/env python3
"""Read-only MMF history reconciliation; writes preview reports, never history."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.release_rules import release_month, release_directory_name


def stamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except (AttributeError, ValueError, TypeError):
        return None


def identity(value):
    return value['object_id'], value['archive_id']


def receipt_status(item, receipt, completed):
    if item['key'] in completed:
        return 'Recorded current version'
    if receipt is None:
        return 'No legacy receipt'
    if receipt['filename'].split('^^')[-1] != item['filename']:
        return 'Review: filename differs'
    downloaded, updated = stamp(receipt.get('reported_downloaded_at')), stamp(item.get('updated_at'))
    if downloaded is None or updated is None:
        return 'Review: revision date unavailable'
    if updated > downloaded:
        return 'Updated after legacy download'
    return 'Candidate: no newer reported update (unverified bytes)'


def special_release(label):
    # "September 2026 Standard Rewards" is monthly; "1 Year Rewards" is not.
    return bool(re.search(r'\b(welcome|loyalty|bonus)\b|\b\d+\s*(months?|years?)\b|subscriber reward', label, re.I))


def folder_match(item, base):
    artist = base / item['folder']
    if artist.parent != base or artist.is_symlink() or not artist.is_dir():
        return 'Artist folder unavailable', '', []
    label = item.get('release') or item.get('object_name') or ''
    special = special_release(label)
    month = None if special else release_month(label) or item.get('release_month')
    dirs = [p for p in artist.iterdir() if p.is_dir() and not p.is_symlink()]
    if month:
        names = {month.casefold(), release_directory_name(item['folder'], month).casefold()}
        matches = [p for p in dirs if p.name.casefold() in names]
        kind = 'Artist/month match'
    else:
        matches = [p for p in dirs if p.name.casefold() == item['release_folder'].casefold()]
        kind = 'Exact named-folder match'
    if len(matches) == 1:
        archives = [p.name for p in matches[0].iterdir() if p.is_file() and not p.is_symlink()
                    and re.search(r'\.(zip|rar|7z)(\.\d+)?$', p.name, re.I)]
        return kind if archives else kind + ' (no direct archives)', str(matches[0]), archives
    if len(matches) > 1:
        return 'Review: multiple folder matches', '', [p.name for p in matches]
    # Suggestions are always manual, never treated as evidence of a match.
    tokens = set(re.findall(r'[a-z0-9]+', label.lower())) - {'the','a','with','and','pack','rewards','reward'}
    suggestions = sorted(dirs, key=lambda p: (-len(tokens & set(re.findall(r'[a-z0-9]+', p.name.lower()))), p.name))
    suggestions = [p.name for p in suggestions if len(tokens & set(re.findall(r'[a-z0-9]+', p.name.lower()))) >= 2][:3]
    return 'Review: named collection' if special or not month else 'No monthly folder', '', suggestions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    db = sqlite3.connect(f'file:{ROOT / "data/mmf/manager.sqlite3"}?mode=ro', uri=True)
    state = {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM state')}
    completed = {k for k, in db.execute('SELECT key FROM completed')}
    legacy = json.loads((ROOT / 'data/mmf/legacy-log-receipts.json').read_text())
    receipts = {identity(r): r for r in legacy['receipts']}
    base = Path(json.loads((ROOT / 'data/settings.json').read_text())['download_directory'])
    if not base.is_dir():
        raise SystemExit('Download base unavailable; no trustworthy folder preview possible.')
    rows = []
    folders = {}
    for item in state.get('items', []):
        receipt = receipts.get(identity(item))
        key = (item['folder'], item['release_folder'])
        if key not in folders:
            folders[key] = folder_match(item, base)
        match, destination, evidence = folders[key]
        rows.append({'artist': item['creator'], 'release': item.get('release') or item['release_folder'],
                     'filename': item['filename'], 'object_id': item['object_id'], 'archive_id': item['archive_id'],
                     'history_status': receipt_status(item, receipt, completed),
                     'logged_download': receipt.get('reported_downloaded_at', '') if receipt else '',
                     'mmf_updated': item.get('updated_at', ''), 'folder_status': match,
                     'existing_folder': destination, 'folder_evidence_or_suggestions': ' | '.join(evidence),
                     'current_key': item['key']})
    current_ids = {identity(i) for i in state.get('items', [])}
    summary = {'dry_run': True, 'created_at': datetime.now(timezone.utc).isoformat(),
               'logs': legacy['log_count'], 'success_markers': legacy['success_pairs'],
               'unique_legacy_receipts': len(receipts), 'current_files': len(rows),
               'receipts_outside_current_scan': len(set(receipts) - current_ids),
               'history_status': dict(Counter(r['history_status'] for r in rows)),
               'folder_status_per_release': dict(Counter(v[0] for v in folders.values()))}
    per_artist = defaultdict(Counter)
    for row in rows:
        per_artist[row['artist']][row['history_status']] += 1
    summary['artists'] = {k: dict(v) for k, v in per_artist.items()}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'preview.json').write_text(json.dumps({'summary': summary, 'files': rows}, indent=2))
    with (args.output / 'files.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ['artist'])
        writer.writeheader(); writer.writerows(rows)
    esc = lambda value: html.escape(str(value))
    body = ['<!doctype html><meta charset="utf-8"><title>MMF history preview</title>',
            '<style>body{font:15px system-ui;margin:32px;color:#292332}table{border-collapse:collapse;width:100%}td,th{padding:9px;border:1px solid #ddd;text-align:left}th{background:#eee}td{overflow-wrap:anywhere}h1{color:#7452a1}</style>',
            '<h1>MMF history — dry-run preview</h1><p>No download history or release files were changed. '
            'Receipt candidates are evidence of a past download, not checksum verification of the current file. '
            'Existing repacked archives do not establish which individual MMF versions they contain. '
            'Named-folder suggestions require review. Files updated after a receipt must remain eligible for downloading.</p>',
            '<h2>Summary</h2><pre>' + esc(json.dumps(summary, indent=2)) + '</pre>',
            '<h2>Files</h2><table><tr>' + ''.join('<th>'+esc(k)+'</th>' for k in ('Artist / release','File','History','Folder')) + '</tr>']
    for row in rows:
        body.append('<tr>' + ''.join('<td>'+esc(value)+'</td>' for value in (
            row['artist']+' / '+row['release'], row['filename'], row['history_status'],
            row['folder_status']+' — '+(row['existing_folder'] or row['folder_evidence_or_suggestions']))) + '</tr>')
    body.append('</table>')
    (args.output / 'preview.html').write_text('\n'.join(body))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
