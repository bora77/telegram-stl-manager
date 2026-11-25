"""Opt-in downloads driven by browser ticks, never an unattended scheduler."""
import fcntl
import json
from pathlib import Path


class AutoDownloads:
    providers=('telegram','mmf')

    def __init__(self,store,availability,mmf):
        self.store=store;self.availability=availability;self.mmf=mmf
        self.path=store.root/'data/auto-downloads.json'
        self.lock_path=store.root/'data/auto-downloads.lock'

    def read(self):
        saved=json.loads(self.path.read_text()) if self.path.exists() else {}
        return {p:{'enabled':False,'last_check':None,'message':'',**saved.get(p,{})} for p in self.providers}

    def save(self,payload):
        provider=payload.get('provider');enabled=payload.get('enabled')
        if provider not in self.providers or type(enabled) is not bool:raise ValueError('Choose Telegram or MMF and an enabled/disabled state.')
        with self.lock_path.open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            state=self.read();state[provider].update(enabled=enabled,message='')
            self.store.atomic_write(self.path,state)
            return state

    def tick(self):
        with self.lock_path.open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            state=self.read()
            for provider in self.providers:
                entry=state[provider]
                if not entry['enabled']:continue
                try:self.start_detected(provider,entry,state)
                except Exception as error:entry['message']='Auto-download could not start: '+str(error)
            self.store.atomic_write(self.path,state)
            return state

    def start_detected(self,provider,entry,state):
        config=self.store.config()
        if provider=='telegram':
            current=self.store.runs.status()
            if current['state'] in ('starting','scanning','downloading','extracting','copying','stopping'):
                entry['message']='A download is running.';return
            if current['state'] not in ('idle','completed'):
                entry['message']='Resume or resolve the unfinished download run manually.';return
            available=self.availability.status();checked=available.get('checked_at')
            if available.get('checking') or available.get('errors') or not available.get('files') or available.get('claimed'):return
            payload={'revision':self.store.read()['revision'],'config_revision':config['revision'],'mode':'detected'}
            start=lambda:self.store.runs.start(payload)
        else:
            if not config.get('mmf_beta_enabled') or not self.mmf.session.exists() or self.mmf.get('auth_required',False):return
            if self.mmf.busy():entry['message']='An MMF task is running.';return
            known=self.mmf.completed()-self.mmf.pending_redownload_keys()
            if any(i['key'] not in known for i in self.mmf.get('queue',[])):
                entry['message']='Resume or resolve the unfinished MMF run manually.';return
            job=self.mmf.get('job',{})
            if job.get('action')=='check' and job.get('phase')!='complete':return
            checked=self.mmf.get('checked_at',0)
            if checked==self.mmf.get('download_checked_at',0):return
            if not any(i['key'] not in known for i in self.mmf.scoped_items(self.mmf.get('items',[]))):return
            start=lambda:self.mmf.start('download')
        if not checked or entry['last_check']==checked:return
        if not Path(config['download_directory']).is_dir():
            entry['message']='Waiting for the download folder to become available.';return
        previous=entry['last_check'];entry['last_check']=checked;entry['message']='Starting detected downloads.'
        # Claim before launching: concurrent tabs and a server restart cannot
        # repeatedly launch a failed batch from the same availability result.
        self.store.atomic_write(self.path,state)
        try:start()
        except FileExistsError:
            entry['last_check']=previous;entry['message']='Waiting for the current task to finish.'
        else:entry['message']='Detected downloads started automatically.'
