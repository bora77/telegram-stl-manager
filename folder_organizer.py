"""Read-only folder plans matched against exact CLI filenames and sizes."""
import fcntl
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from functools import lru_cache

from file_delivery import mount_identity
from file_size import display_size, matches_size
from release_rules import release_month
from subscription_store import SubscriptionStore
from telegram_cli import TelegramCLI

ROOT = Path(__file__).resolve().parent
ACTIVE = ('starting', 'scanning', 'applying')


def inventory(folder):
    """Inspect immediate files and existing month folders; never create anything."""
    folder = Path(folder)
    rows = []
    candidates = list(folder.iterdir())
    for child in tuple(candidates):
        if not child.is_symlink() and child.is_dir() and re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', child.name):
            candidates.extend(child.iterdir())
    for path in sorted(candidates):
        if path.is_symlink() or not path.is_file():
            continue
        relative = str(path.relative_to(folder))
        month = release_month(path.name)
        target = folder / month / path.name if month else None
        action = 'review' if month is None else 'keep' if path == target else 'conflict' if target.exists() else 'move'
        info=path.stat()
        rows.append({'filename': path.name, 'source': relative, 'size': info.st_size,
                     'identity': {'size':info.st_size,'mtime_ns':info.st_mtime_ns,'device':info.st_dev,'inode':info.st_ino},
                     'month': month, 'destination': str(target.relative_to(folder)) if target else None,
                     'action': action, 'telegram_status': 'pending', 'would_record': False,
                     'reason': 'Release month is unclear.' if not month else 'Destination already exists; review both files.' if action == 'conflict' else ''})
    targets={}
    for row in rows:
        if row['destination']:targets.setdefault(row['destination'].casefold(),[]).append(row)
    for group in targets.values():
        if len(group)>1:
            for row in group:
                if row['action']=='move':
                    row.update(action='conflict',reason='Multiple existing files would use the same destination; review required.')
    return rows


@lru_cache(maxsize=4096)
def name_key(name):
    # Tolerate spacing/punctuation and the common OCR rendering of .7z as .72.
    name=re.sub(r'\.72(?=\.|$)', '.7z', name.strip(), flags=re.I)
    return ''.join(c for c in name.casefold() if c.isalnum())


@lru_cache(maxsize=65536)
def name_score(local,displayed):
    actual=name_key(local);observed=name_key(displayed)
    if not observed:return 0
    if actual==observed:return 1
    # Telegram may clip long filenames. The size must still identify one local
    # filename, and overlapping candidate names are explicitly ambiguous.
    clipped=displayed.rstrip().endswith(('...','…','..')) or not re.search(r'\.(?:7z|72|zip|rar|jpg|jpeg|png|webp|gif|stl|\d{3})$',displayed,re.I)
    if clipped and len(observed)>=20:
        if actual.startswith(observed):return .96
        prefix=actual[:len(observed)]
        if re.findall(r'\d+',prefix)==re.findall(r'\d+',observed):
            ratio=SequenceMatcher(None,prefix,observed).ratio()
            if ratio>=.93:return .93
    if len(observed)>=20 and actual.endswith(observed):return .95
    if re.findall(r'\d+',actual)!=re.findall(r'\d+',observed):return 0
    ratio=SequenceMatcher(None,actual,observed).ratio()
    return ratio if ratio>=.9 else 0


def match_files(files, attachments, downloaded, complete=True):
    """Map displayed names and rounded sizes, without opening any messages."""
    if all('bytes_total' in item for item in attachments):
        return match_exact_files(files,attachments,downloaded,complete)
    found={i:[] for i in range(len(files))};ambiguous=set();name_seen=set();evidence={i:[] for i in range(len(files))}
    for item in attachments:
        choices=[]
        for i,file in enumerate(files):
            score=name_score(file['filename'],item['filename'])
            if not score:continue
            name_seen.add(i)
            evidence[i].append((score,item))
            if matches_size(file['size'],display_size(item.get('size_text',''))):
                choices.append((i,score))
        if not choices:continue
        best=max(score for _,score in choices)
        choices=[(i,score) for i,score in choices if score>=best-.015]
        keys={name_key(files[i]['filename']) for i,_ in choices}
        if len(keys)>1:
            ambiguous.update(i for i,_ in choices);continue
        for i,score in choices:found[i].append((score,item))
    for i,file in enumerate(files):
        for field in ('message_url','source_message_id','telegram_size','telegram_name','match_kind'):file.pop(field,None)
        file['already_recorded']=(file['filename'],file['size']) in downloaded
        file['would_record']=False
        if evidence[i]:
            _,item=max(evidence[i],key=lambda match:match[0])
            file.update(telegram_name=item['filename'],telegram_size=item.get('size_text',''))
        if found[i]:
            score,item=max(found[i],key=lambda match:match[0])
            file.update(telegram_status='matched',telegram_name=item['filename'],telegram_size=item['size_text'],
                        match_kind='Name + size' if score==1 else 'Displayed name + size')
            file['would_record']=not file['already_recorded'] and file.get('action') in ('move','keep')
        else:
            readable=display_size(file.get('telegram_size','')) is not None
            file['telegram_status']='ambiguous' if i in ambiguous else ('size_mismatch' if readable else 'size_unreadable') if i in name_seen else 'unmatched' if complete else 'pending'


def match_exact_files(files, attachments, downloaded, complete=True):
    by_name={}
    for item in attachments:by_name.setdefault(item['filename'],[]).append(item)
    for file in files:
        for key in ('message_url','source_message_id','source_message_ids','telegram_size','telegram_bytes','telegram_name','match_kind'):
            file.pop(key,None)
        file.update(already_recorded=(file['filename'],file['size']) in downloaded,would_record=False)
        candidates=by_name.get(file['filename'],[])
        matched=[item for item in candidates if item['bytes_total']==file['size']]
        if matched:
            item=matched[-1]
            file.update(telegram_status='matched',telegram_name=item['filename'],telegram_bytes=item['bytes_total'],
                        telegram_size=f"{item['bytes_total']:,} bytes",match_kind='Exact filename + bytes',
                        source_message_ids=[m['source_message_id'] for m in matched],
                        source_message_id=item['source_message_id'],message_url=item['message_url'])
            file['would_record']=not file['already_recorded'] and file.get('action') in ('move','keep')
        elif candidates:
            item=candidates[-1]
            file.update(telegram_status='size_mismatch',telegram_name=item['filename'],telegram_bytes=item['bytes_total'],telegram_size=f"{item['bytes_total']:,} bytes")
        else:file['telegram_status']='unmatched' if complete else 'pending'


class Organizer:
    def __init__(self, store):
        self.store = store
        self.root = store.root
        self.path = self.root / 'data/organizer-plan.json'

    def status(self):
        if not self.path.exists():
            plan={'state': 'idle', 'dry_run': True, 'files': [], 'message': 'Choose an artist and folder to preview.'}
        else:plan = json.loads(self.path.read_text())
        if plan['state'] in ACTIVE and time.time() - plan.get('heartbeat', 0) > 60:
            with (self.root / 'data/organizer-worker.lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    plan.update(state='interrupted', message='Preview interrupted. Run another dry run to continue.')
        from organizer_apply import application_status, RUNNING
        job=application_status(self.store)
        if job:
            plan['application']=job
            if job['state'] in RUNNING:plan.update(state='applying',message=job['message'])
        return plan

    def start(self, payload):
        if payload.get('dry_run') is not True:
            raise ValueError('Only dry runs are enabled. No file or history changes can be applied.')
        catalog = json.loads((self.root / 'data/creators.json').read_text())['creators']
        creator = next((c for c in catalog if c['topic_url'] == payload.get('topic_url') and c['within_approved_group']), None)
        if not creator:
            raise ValueError('Choose an artist from the approved Table of Contents.')
        folder = payload.get('creator_folder')
        if not isinstance(folder, str) or folder in ('', '.', '..') or re.search(r'[\\/\x00]', folder):
            raise ValueError('Choose one artist folder under the configured download directory.')
        base = Path(self.store.config()['download_directory'])
        mount_identity(base)
        target = base / folder
        if target.is_symlink() or not target.is_dir() or target.resolve().parent != base.resolve():
            raise ValueError('Artist folder must exist inside the configured download directory.')
        with (self.root / 'data/operation.lock').open('a') as operation:
            fcntl.flock(operation, fcntl.LOCK_EX)
            if self.status()['state'] in ACTIVE:
                raise FileExistsError('Another folder operation is active. Wait for it to finish.')
            plan = {'id': uuid.uuid4().hex, 'state': 'starting', 'dry_run': True,
                    'creator': creator['name'], 'topic_url': creator['topic_url'], 'creator_folder': folder,
                    'base': str(base), 'files': inventory(target), 'attachments': [], 'warnings': [], 'comparison_mode':'filename_and_size',
                    'heartbeat': time.time(), 'started_at': datetime.now(timezone.utc).isoformat(),
                    'backend':'cli','message': 'Reading exact Telegram filenames and sizes.'}
            self.store.atomic_write(self.path, plan)
            (self.root / 'data/organizer-stop').unlink(missing_ok=True)
            with (self.root / 'data/organizer.log').open('ab') as log:
                subprocess.Popen(['python3', str(self.root / 'folder_organizer.py'), plan['id']], cwd=self.root,
                                 stdout=log, stderr=log, start_new_session=True)
        return plan

    def stop(self):
        (self.root / 'data/organizer-stop').touch(mode=0o600)
        return {'message': 'Stop requested. The preview or saved organization progress will remain available.'}


def scan_plan(store, plan_id):
    organizer = Organizer(store)
    plan = organizer.status()
    if plan.get('id') != plan_id:
        return
    ui = None
    def save(message=None, **values):
        plan.update(heartbeat=time.time(), **values)
        if message is not None:
            plan['message'] = message
        # History is read only for this operation, including matched files.
        with store.history.connect() as db:
            downloaded = {(r['filename'],r['bytes_total']) for r in db.execute("SELECT filename,bytes_total FROM downloads WHERE state='downloaded' AND topic_url=?",(plan['topic_url'],))}
        match_files(plan['files'], plan['attachments'], downloaded,complete=plan['state'] not in ACTIVE)
        store.atomic_write(organizer.path, plan)
    try:
        ui = TelegramCLI(root=store.root,config=store.config())
        topic = plan['topic_url']
        stopped=lambda:(store.root/'data/organizer-stop').exists()
        plan.update(rows_read=0,attachments=[],backend='cli')
        def status(message):save(plan['creator']+' · '+message,state='scanning')
        def on_files(files):
            plan['attachments'].extend(files)
            save('Comparing exact filenames and sizes…',state='scanning',rows_read=len(plan['attachments']))
        plan['attachments']=ui.list_files(topic,stopped,status,on_files=on_files)
        plan.update(telegram_file_count=len(plan['attachments']),rows_read=len(plan['attachments']))
        save('Dry run complete: filenames and sizes mapped. No files moved and no download records changed.', state='completed', finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        save(str(error), state='stopped' if (store.root/'data/organizer-stop').exists() else 'failed')
    finally:
        if ui:ui.close()


if __name__ == '__main__':
    with (ROOT / 'data/organizer-worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        scan_plan(SubscriptionStore(ROOT), sys.argv[1])
