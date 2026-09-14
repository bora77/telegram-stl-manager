"""A single, explicitly requested batch. No polling scheduler or automatic restart."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid
from datetime import datetime,timezone
from release_images import image_warnings


def normalize_run_warnings(data):
    previous=data.get('warnings',[])
    data['warnings']=image_warnings(previous)
    if data.get('state')=='needs_review' and previous and not data['warnings']:
        data.update(state='completed',message='Manual run finished. All eligible discovered files are handled.')


def update_run_outcome(data,history):
    if data.get('id') and data.get('state') in ('completed','needs_review'):
        data.update(history.batch_outcome(data['id']))
        if data['unfinished_files']:
            data.update(state='needs_review',message=f"Run ended with unfinished files: {data['completed_files']} / {data['total_files']} complete. Resume retries the remaining {data['unfinished_files']} files using saved artist checks and verified local downloads.")
        elif data.get('plan_version')==1 and not data.get('warnings'):
            data.update(state='completed',message='Run complete. All eligible discovered files are handled.')


def current_run_warnings(data,history):
    if data.get('plan_version')!=1 or not data.get('id'):return
    scans=data.get('scan_warnings')
    if scans is None:
        scans=[] if data.get('resume_requested') else [w for w in data.get('warnings',[]) if ': release month needs review for ' in w or ': unsafe attachment filename needs review.' in w]
    problems=[] if data.get('state') in ('starting','scanning') else history.run_warnings(data['id'])
    data['warnings']=list(dict.fromkeys(scans+problems))


class RunManager:
    def __init__(self,store):
        self.store=store;self.root=store.root;self.path=self.root/'data/run.json';self.lock=threading.Lock();self.process=None
    def status(self):
        if not self.path.exists():return {'state':'idle','message':'Ready for a manual run.','warnings':[]}
        data=json.loads(self.path.read_text())
        normalize_run_warnings(data)
        current_run_warnings(data,self.store.history)
        update_run_outcome(data,self.store.history)
        if data['state'] in ('starting','scanning','downloading','extracting','copying','stopping'):
            lockpath=self.root/'data/worker.lock'
            with lockpath.open('a') as stream:
                try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:pass
                else:
                    if data['state']!='starting' or (datetime.now(timezone.utc)-datetime.fromisoformat(data.get('launch_at') or data['started_at'])).total_seconds()>15:
                        data['state']='interrupted';data['message']='The previous run stopped. Resume the saved queue; staged files and history were kept.'
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
                'subscriptions':settings['subscriptions'],'download_directory':config['download_directory'],'config':config,'backend':'cli','creators_done':0,'creators_total':len(settings['subscriptions']),'plan_version':1}
            (self.root/'data/stop-request').unlink(missing_ok=True)
            self.store.atomic_write(self.path,run)
            with (self.root/'data/worker.log').open('ab') as log:
                self.process=subprocess.Popen(['python3',str(self.root/'download_worker.py'),batch],cwd=self.root,stdout=log,stderr=log,start_new_session=True)
            return run
    def resume(self,payload):
        with self.lock,(self.root/'data/operation.lock').open('a') as operation:
            fcntl.flock(operation,fcntl.LOCK_EX)
            run=self.status()
            if not (run['state'] in ('failed','stopped','interrupted') or run['state']=='needs_review' and run.get('unfinished_files',0)):
                raise FileExistsError('There are no unfinished downloads to resume.')
            if not run.get('id') or payload.get('run_id')!=run['id']:raise FileExistsError('The download run changed. Refresh the queue before resuming.')
            if not run.get('subscriptions'):raise ValueError('The saved subscriptions are missing; start a new run.')
            with (self.root/'data/worker.lock').open('a') as worker:
                try:fcntl.flock(worker,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:raise FileExistsError('The previous download worker is still finishing. Resume again shortly.')
            config=self.store.config()
            if Path(config['download_directory']).resolve()!=Path(run['download_directory']).resolve():
                raise ValueError('The destination changed. Restore the saved run’s download folder or start a new run.')
            from download_plan import DownloadPlan
            DownloadPlan(self.store,run) # Validate saved source and run identity before launch.
            now=datetime.now(timezone.utc).isoformat()
            run.update(state='starting',message='Resuming the saved queue…',resumed_at=now,launch_at=now,resume_requested=True,
                       resume_count=run.get('resume_count',0)+1,config=config)
            run.pop('finished_at',None)
            (self.root/'data/stop-request').unlink(missing_ok=True)
            self.store.atomic_write(self.path,run)
            try:
                with (self.root/'data/worker.log').open('ab') as log:
                    self.process=subprocess.Popen(['python3',str(self.root/'download_worker.py'),run['id']],cwd=self.root,stdout=log,stderr=log,start_new_session=True)
            except OSError as error:
                run.update(state='failed',message='Could not launch the download worker; the saved queue was kept.')
                self.store.atomic_write(self.path,run)
                raise ValueError(run['message']) from error
            return run
    def stop(self):
        (self.root/'data/stop-request').touch(mode=0o600)
        return {'message':'Stop requested. Completed staged files are retained; incomplete transfers restart on retry.'}
