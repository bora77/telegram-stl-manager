"""Validated local subscription settings; saving never starts a transfer."""
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from datetime import datetime, timezone
from app.download_history import DownloadHistory
from app.release_rules import baseline_month
from app.run_manager import RunManager, normalize_run_warnings, current_run_warnings, update_run_outcome
from app.release_images import image_warnings
from app.source_scope import load_source

INCOMING = '!! 3D STLs - INCOMING'

class SubscriptionStore:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'data/subscriptions.json'
        self.config_path = self.root / 'data/settings.json'
        self.lock = threading.Lock()
        self.history=DownloadHistory(self.root / 'data/download-history.sqlite3')
        self.runs=RunManager(self)

    def config(self):
        data=json.loads(self.config_path.read_text()) if self.config_path.exists() else {'revision':0,'download_directory':str(self.root/'downloads')}
        data.setdefault('download_storage','network' if str(data.get('download_directory','')).startswith(('/mnt/kronos-stl/','/mnt/telegram-stl-share/')) else 'local')
        data.setdefault('mmf_beta_enabled',False)
        data.setdefault('release_pad_destination','')
        data.setdefault('download_servers',{})
        data.setdefault('server_check_frequency','cached')
        data.setdefault('server_speed_threshold_mbps',0)
        data.setdefault('download_bandwidth_target_mbps',28)
        data.setdefault('adaptive_downloads',True)
        data.setdefault('availability_interval_hours',4)
        data.setdefault('mmf_availability_interval_hours',4)
        data.setdefault('release_compression_level',7)
        data.setdefault('release_volume_mib',4000)
        return data

    def atomic_write(self, path, data):
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd,name=tempfile.mkstemp(dir=path.parent,prefix='.'+path.stem+'-')
        try:
            with os.fdopen(fd,'w') as stream:
                json.dump(data,stream,ensure_ascii=False,indent=2)
                stream.flush();os.fsync(stream.fileno())
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)

    def save_config(self,payload):
        if not isinstance(payload,dict):raise ValueError('Invalid settings.')
        value=payload.get('download_directory')
        if isinstance(value,str) and Path('/etc/telegram-stl-managed').exists() and re.match(r'^[A-Za-z]:[\\/]',value):
            value='/mnt/'+value[0].lower()+'/'+value[3:].replace('\\','/')
        if not isinstance(value,str) or not value or '\x00' in value or not Path(value).is_absolute():
            raise ValueError('Enter an absolute folder path on this machine.')
        directory=Path(value).resolve()
        if not directory.is_dir() or not os.access(directory,os.R_OK|os.W_OK|os.X_OK):
            if isinstance(payload.get('download_directory'),str) and Path('/etc/telegram-stl-managed').exists() and re.match(r'^[A-Za-z]:[\\/]',payload['download_directory']):
                raise ValueError('This Windows folder is not accessible to the manager. If it is a mapped NAS or shared drive, choose Network share and enter its full network path instead of the drive letter. Otherwise, choose an existing local folder with read and write access.')
            raise ValueError('Choose an existing folder that your account can read and write.')
        with self.lock:
            current=self.config()
            if payload.get('revision')!=current['revision']:raise FileExistsError('Settings changed in another tab. Reload before saving.')
            mmf_beta=payload.get('mmf_beta_enabled',current['mmf_beta_enabled'])
            if type(mmf_beta) is not bool:raise ValueError('Choose whether to show the MyMiniFactory beta.')
            release_pad=payload.get('release_pad_destination',current['release_pad_destination'])
            if not isinstance(release_pad,str):raise ValueError('Enter a Release Pad username or numeric chat ID.')
            release_pad=release_pad.strip()
            if release_pad.startswith('https://t.me/'):
                release_pad='@'+release_pad[len('https://t.me/'):].rstrip('/')
            if release_pad and not re.fullmatch(r'(?:@[A-Za-z][A-Za-z0-9_]{3,31}|-?[1-9][0-9]{0,18})',release_pad):
                raise ValueError('Use @username, https://t.me/username, or a numeric chat ID for a private Release Pad. Invite and message links are not destinations.')
            storage=payload.get('download_storage',current['download_storage'])
            if storage not in ('local','network'):raise ValueError('Choose local storage or a network share.')
            from app.telegram_cli import validate_servers
            servers=validate_servers(payload.get('download_servers',current['download_servers']),self.root)
            frequency=payload.get('server_check_frequency',current['server_check_frequency'])
            if frequency not in ('cached','release'):raise ValueError('Choose a valid server comparison frequency.')
            threshold=payload.get('server_speed_threshold_mbps',current['server_speed_threshold_mbps'])
            if type(threshold) not in (int,float) or not 0<=threshold<=1000 or not math.isfinite(threshold):
                raise ValueError('Enter a speed threshold between 0 and 1000 MB/s. Zero disables slowdown checks.')
            target=payload.get('download_bandwidth_target_mbps',current['download_bandwidth_target_mbps'])
            if type(target) not in (int,float) or not 0<target<=1000 or not math.isfinite(target):
                raise ValueError('Enter a total download bandwidth target greater than 0 and at most 1000 MB/s.')
            interval=payload.get('availability_interval_hours',current['availability_interval_hours'])
            if type(interval) not in (int,float) or not math.isfinite(interval) or not .25<=interval<=168:
                raise ValueError('Choose an availability check interval between 0.25 and 168 hours.')
            mmf_interval=payload.get('mmf_availability_interval_hours',current['mmf_availability_interval_hours'])
            if type(mmf_interval) not in (int,float) or not math.isfinite(mmf_interval) or not .25<=mmf_interval<=168:
                raise ValueError('Choose an MMF check interval between 0.25 and 168 hours.')
            compression=payload.get('release_compression_level',current['release_compression_level'])
            volume=payload.get('release_volume_mib',current['release_volume_mib'])
            if type(compression) is not int or compression not in (0,1,3,5,7,9):
                raise ValueError('Choose a compression level of 0, 1, 3, 5, 7 or 9.')
            if type(volume) is not int or not 1<=volume<=65536:
                raise ValueError('Choose a release part size between 1 and 65536 MiB.')
            adaptive=payload.get('adaptive_downloads',current['adaptive_downloads'])
            if type(adaptive) is not bool:raise ValueError('Choose whether adaptive parallel downloads are enabled.')
            data={'revision':current['revision']+1,'download_storage':storage,'mmf_beta_enabled':mmf_beta,'release_pad_destination':release_pad,'download_directory':str(directory),'download_servers':servers,'server_check_frequency':frequency,'server_speed_threshold_mbps':threshold,'download_bandwidth_target_mbps':target,'adaptive_downloads':adaptive,'availability_interval_hours':interval,'mmf_availability_interval_hours':mmf_interval,'release_compression_level':compression,'release_volume_mib':volume}
            self.atomic_write(self.config_path,data)
            return data

    def config_view(self):
        data=self.config()
        try:
            destinations=json.loads((self.root/'data/release-pad-destinations.json').read_text()).get('destinations',[])
            data['release_pad_title']=next((r['title'] for r in destinations if r['id']==data['release_pad_destination']),'')
        except (OSError,ValueError,KeyError,TypeError):data['release_pad_title']=''
        data['local_download_directory']=str(self.root/'downloads')
        from app.telegram_cli import server_view
        data.update(telegram_servers=server_view(self.root),download_backend='cli')
        try:
            with os.scandir(data['download_directory']) as entries:
                data['folders']=sorted((e.name for e in entries if e.is_dir(follow_symlinks=False) and not e.name.startswith('- !')),key=str.casefold)
            data['available']=True
        except OSError:
            data['folders']=[];data['available']=False
        return data

    def read(self):
        if self.path.exists():
            data=json.loads(self.path.read_text())
            for record in data['subscriptions']:
                record.setdefault('subscribed_at',data['saved_at'])
                record.setdefault('start_month',baseline_month(record['subscribed_at']))
                if record.get('download_scope') == 'future':record['download_scope'] = 'from_month'
            return data
        return {'version':2, 'revision':0, 'subscriptions':[], 'saved_at':None}

    def save(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get('subscriptions'), list):
            raise ValueError('Expected a subscriptions list.')
        catalog = json.loads((self.root / 'data/creators.json').read_text())
        source = load_source(self.root)
        allowed = {r['topic_url']:r for r in catalog['creators'] if r['within_approved_group']}
        if len(payload['subscriptions']) > len(allowed):
            raise ValueError('Too many subscriptions.')
        records, seen = [], set()
        for entry in payload['subscriptions']:
            if not isinstance(entry, dict):
                raise ValueError('Invalid subscription.')
            url = entry.get('topic_url')
            if not isinstance(url,str) or url not in allowed or source.topic_id(url) is None:
                raise ValueError('A creator is outside the approved catalog.')
            if url in seen:
                raise ValueError('The same topic was selected more than once.')
            seen.add(url)
            folder = entry.get('creator_folder')
            if not isinstance(folder,str):raise ValueError('Choose a creator folder below the configured download folder.')
            if not folder or folder.strip()!=folder or folder in ('.','..') or re.search(r'[\\/<>:"|?*\x00-\x1f]',folder) or folder.endswith(('.', ' ')) or folder.startswith('- !'):
                raise ValueError('Invalid creator folder.')
            if entry.get('layout') != 'monthly' or entry.get('month_basis') != 'release':
                raise ValueError('Monthly folders must use the release month.')
            if entry.get('download_scope') not in ('from_month','all_and_future'):
                raise ValueError('Choose a download scope for each creator.')
            if entry['download_scope']=='from_month' and not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',str(entry.get('start_month',''))):
                raise ValueError('Choose the starting month and year.')
            records.append({'start_month':entry.get('start_month') if entry['download_scope']=='from_month' else None,'creator':allowed[url]['name'],'topic_url':url,'creator_folder':folder,
                'layout':'monthly','month_basis':'release','month_format':'YYYY-MM','unknown_month':'needs_review',
                'download_scope':entry['download_scope'],'state':'awaiting_downloader'})
        with self.lock:
            current = self.read()
            if payload.get('config_revision')!=self.config()['revision']:
                raise FileExistsError('The download folder changed. Reload settings before saving subscriptions.')
            if payload.get('revision') != current['revision']:
                raise FileExistsError('Subscriptions changed in another tab. Reload saved settings before saving.')
            previous={r['topic_url']:r for r in current['subscriptions']}
            now=datetime.now(timezone.utc).isoformat()
            for record in records:
                old=previous.get(record['topic_url'],{})
                record['subscribed_at']=old.get('subscribed_at',current.get('saved_at') if old else now) or now
                if record['download_scope']=='all_and_future':record['start_month']=old.get('start_month') or baseline_month(record['subscribed_at'])
                record['state']='saved'
            data = {'version':2,'revision':current['revision']+1,'saved_at':datetime.now(timezone.utc).isoformat(),
                'subscriptions':records}
            self.atomic_write(self.path,data)
            return data

    def task_status(self):
        """Small, read-only completion snapshots; no catalog scans or NAS access."""
        tasks={}
        for kind,name in (('download','run.json'),('organize_preview','organizer-plan.json'),('organize_apply','organizer-apply.json')):
            path=self.root/'data'/name
            try:
                with path.open() as stream:
                    data=json.load(stream);version=str(os.fstat(stream.fileno()).st_mtime_ns)
            except FileNotFoundError:data={};version='0'
            if kind=='download':
                current_run_warnings(data,self.history)
                normalize_run_warnings(data)
                update_run_outcome(data,self.history)
            tasks[kind]={key:data.get(key) for key in ('id','state','started_at','finished_at','creator')}
            tasks[kind]['state']=data.get('state','idle')
            tasks[kind]['version']=version
            tasks[kind]['needs_review']=bool(data.get('state')=='needs_review' or image_warnings(data.get('warnings')) or data.get('releases_review')
                or any(image_warnings(group.get('image_warnings')) or group.get('error') for group in data.get('groups',[])))
        return {'tasks':tasks}

    def queue(self):
        from app.folder_organizer import Organizer, ACTIVE
        run=self.runs.status()
        active=run['state'] in ('starting','scanning','downloading','extracting','copying','stopping')
        organizer_active=Organizer(self).status()['state'] in ACTIVE
        count=len(self.read()['subscriptions'])
        queue=self.history.queue(run.get('id'))
        from app.run_manager import corrupt_download
        for file in queue['files']:file['corrupt_archive']=corrupt_download(file)
        transferring=[f for f in queue['files'] if f['state']=='downloading' and f.get('download_finished_at') is None] if active and run['state']=='downloading' else []
        measured=[f['download_speed_bps'] for f in transferring if type(f.get('download_speed_bps')) in (int,float) and math.isfinite(f['download_speed_bps']) and f['download_speed_bps']>=0]
        target=self.config()['download_bandwidth_target_mbps']
        bandwidth={'target_mbps':target,'speed_bps':sum(measured) if measured else None,'active_files':len(transferring)}
        scheduler=None
        if active and run.get('adaptive_scheduler'):
            try:
                saved=json.loads((self.root/'data/download-scheduler.json').read_text())
                if saved.get('batch_id')==run.get('id') and saved.get('active'):
                    scheduler=saved
                    live=[t for t in saved['transfers'] if t['phase']=='downloading']
                    bandwidth.update(speed_bps=sum(t['speed_bps'] for t in live),active_files=len(live),target_mbps=saved['target_mbps'])
            except (OSError,ValueError,KeyError,TypeError):pass
        return {**queue,'bandwidth':bandwidth,'scheduler':scheduler,'worker_state':run['state'],
                'trigger_mode':'manual','can_start':not active and count>0,'active':active,'organizer_active':organizer_active,
                'can_resume':not active and (run['state'] in ('failed','stopped','interrupted') or run['state']=='needs_review' and run.get('unfinished_files',0)>0) and bool(run.get('id') and run.get('subscriptions')),
                'run':run,'subscriptions_saved':count}
