"""A single, explicitly requested batch. No polling scheduler or automatic restart."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid
from datetime import datetime,timezone

class RunManager:
    def __init__(self,store):
        self.store=store;self.root=store.root;self.path=self.root/'data/run.json';self.lock=threading.Lock();self.process=None
    def status(self):
        if not self.path.exists():return {'state':'idle','message':'Ready for a manual run.','warnings':[]}
        data=json.loads(self.path.read_text())
        if data['state'] in ('starting','scanning','downloading','extracting','copying','stopping'):
            lockpath=self.root/'data/worker.lock'
            with lockpath.open('a') as stream:
                try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:pass
                else:
                    if data['state']!='starting' or (datetime.now(timezone.utc)-datetime.fromisoformat(data['started_at'])).total_seconds()>15:
                        data['state']='interrupted';data['message']='The previous run stopped. Press Download all subscriptions to retry; staged files and history were kept.'
        return data
    def start(self,payload):
        with self.lock,(self.root/'data/operation.lock').open('a') as operation:
            fcntl.flock(operation,fcntl.LOCK_EX)
            current=self.status()
            if current['state'] in ('starting','scanning','downloading','extracting','copying','stopping'):raise FileExistsError('A download run is already active.')
            settings=self.store.read();config=self.store.config()
            if payload.get('revision')!=settings['revision'] or payload.get('config_revision')!=config['revision']:raise FileExistsError('Settings changed. Load saved subscriptions before starting.')
            if not settings['subscriptions']:raise ValueError('Save at least one subscription first.')
            batch=uuid.uuid4().hex
            run={'id':batch,'state':'starting','started_at':datetime.now(timezone.utc).isoformat(),'message':'Starting manual run.','warnings':[],
                'subscriptions':settings['subscriptions'],'download_directory':config['download_directory'],'config':config,'backend':'cli','creators_done':0,'creators_total':len(settings['subscriptions'])}
            (self.root/'data/stop-request').unlink(missing_ok=True)
            self.store.atomic_write(self.path,run)
            with (self.root/'data/worker.log').open('ab') as log:
                self.process=subprocess.Popen(['python3',str(self.root/'download_worker.py'),batch],cwd=self.root,stdout=log,stderr=log,start_new_session=True)
            return run
    def stop(self):
        (self.root/'data/stop-request').touch(mode=0o600)
        return {'message':'Stop requested. Completed staged files are retained; incomplete transfers restart on retry.'}
