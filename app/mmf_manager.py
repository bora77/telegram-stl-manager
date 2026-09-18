"""MMF subscriptions, checks, durable queue and isolated manual download worker."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from app.mmf_client import MMFClient, MMFError, LoginRequired
from app.release_rules import release_month, baseline_month, default_creator_folder
from app.release_images import extract_images, is_archive, safe_component, volume_key
from app.file_delivery import deliver, release_lock, digest, DeliveryError
from app.mmf_repack import repack, archive_name, VOLUME_BYTES, POLICY_VERSION, COMPRESSION_LEVEL
from app.release_images import ExtractionError


def month_for(filename,label):
    # MMF creators commonly concatenate month/year, e.g. August2025Reward.zip.
    def parse(text):
        text=re.sub(r'(January|February|March|April|May|June|July|August|September|October|November|December)(20\d{2})',r'\1 \2 ',text,flags=re.I)
        return release_month(text)
    return parse(filename) or parse(label)


def version_key(item):
    return hashlib.sha256(json.dumps([item['object_id'],item['archive_id'],item['size'],item['updated_at']],separators=(',',':')).encode()).hexdigest()


def release_key(item):
    name=item.get('release') or item.get('object_name') or 'Unnamed release'
    month,_=release_date_month(name,item.get('release_created_at'))
    if month:
        return hashlib.sha256(json.dumps([item['creator_id'],['month',month]]).encode()).hexdigest()
    source=['id',str(item['release_id'])] if item.get('release_id') else ['label',item['release']] if item.get('release') else ['object',item['object_id']]
    return hashlib.sha256(json.dumps([item['creator_id'],source]).encode()).hexdigest()


def release_date_month(name, created_at):
    # Special collections remain named even when MMF gives them a creation date.
    if re.search(r'\b(welcome|loyalty|rewards?)\b|^\s*\d+\s*(months?|years?)\b',name,re.I):return None, None
    explicit=month_for('',name)
    if explicit:return explicit,'name'
    try:
        date=datetime.fromisoformat(created_at.replace('Z','+00:00'))
        if not 2000<=date.year<=2099:return None,None
        return date.strftime('%Y-%m'),'created_at'
    except (ValueError,TypeError,AttributeError):return None,None


def releases(items, base=None):
    groups={}
    for item in items:groups.setdefault(release_key(item),[]).append(item)
    for key,group in groups.items():
        name=group[0].get('release') or group[0].get('object_name') or 'Unnamed release'
        month,basis=release_date_month(name,group[0].get('release_created_at'))
        from app.release_rules import release_directory_name
        artist=group[0].get('folder') or group[0].get('creator') or ''
        folder=safe_component(release_directory_name(artist,month,safe_component(name)))
        for item in group:
            item['release_key']=key;item['release_folder']=folder
            item['release_month']=month
            _,item['release_month_basis']=release_date_month(item.get('release') or item.get('object_name') or 'Unnamed release',item.get('release_created_at'))
            if item['release_month_basis']=='created_at' and item.get('release_date_basis')=='delivery_at':item['release_month_basis']='delivery_at'
            item['release_display_name']=folder if month else name
    # Distinct source releases with identical/sanitized names must not merge on disk.
    folders={}
    for key,group in groups.items():
        if not group[0].get('release_month'):folders.setdefault((str(group[0]['creator_id']),group[0]['release_folder'].casefold()),[]).append(key)
    for keys in folders.values():
        if len(keys)>1:
            for key in keys:
                for item in groups[key]:item['release_folder']+='__'+key[:8]
    return groups


class MMFManager:
    def __init__(self,store):
        self.store=store;self.root=store.root;self.directory=self.root/'data/mmf'
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.session=self.directory/'session.cookies';self.database=self.directory/'manager.sqlite3'
        with self.db() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS completed (key TEXT PRIMARY KEY, data TEXT NOT NULL);')
    def db(self):
        db=sqlite3.connect(self.database,timeout=20);db.execute('PRAGMA journal_mode=WAL');return db
    def get(self,key,default=None):
        with self.db() as db:r=db.execute('SELECT value FROM state WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else default
    def put(self,key,value):
        with self.db() as db:db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',(key,json.dumps(value)))
    def client(self):return MMFClient(self.session)
    def completed(self):
        with self.db() as db:return {r[0] for r in db.execute('SELECT key FROM completed')}
    def pending_redownload_keys(self):
        run=self.get('redownload',{})
        if not run:return set()
        with self.db() as db:
            done={key for key,value in db.execute('SELECT key,data FROM completed') if json.loads(value).get('redownload_id')==run['id']}
        return set(run['keys'])-done

    def busy(self):
        with (self.directory/'worker.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return True
        return False
    @contextlib.contextmanager
    def idle(self):
        with (self.directory/'worker.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise FileExistsError('Wait for the current MMF task to finish.')
            yield
    def state(self):
        settings=self.get('settings',{'revision':0,'subscriptions':[]})
        job=self.get('job',{});active=self.busy()
        if not active and job.get('phase') in ('checking','downloading','extracting','moving','starting','unpacking','repacking','verifying_archive','processing_pdfs'):
            job={**job,'phase':'interrupted','message':'Task interrupted. Resume the saved queue.'}
        checked=self.get('checked_at',0);attempt=self.get('attempted_at',0);interval=self.store.config()['mmf_availability_interval_hours']*3600
        known=self.completed()-self.pending_redownload_keys();items=self.get('items',[]);releases(items,self.store.config()['download_directory'])
        from app.mmf_release_prepare import downloaded
        subscribed={str(s['id']) for s in settings.get('subscriptions',[])}
        current_ids={(i['creator_id'],i['object_id'],i['archive_id']) for i in items}
        items.extend(i for i in downloaded(self) if str(i['creator_id']) in subscribed and (i['creator_id'],i['object_id'],i['archive_id']) not in current_ids)
        records={i['key']:i for i in downloaded(self)};path_exists={}
        storage_available=Path(self.store.config()['download_directory']).is_dir()
        for item in items:
            record=records.get(item['key'],{});outputs=(record.get('repack') or {}).get('outputs',[])
            paths=[r['path'] for r in outputs] or ([record['destination']] if record.get('destination') else [])
            missing=[]
            if storage_available:
                for path in paths:
                    if path not in path_exists:
                        try:path_exists[path]=Path(path).is_file()
                        except OSError:path_exists[path]=True
                    if not path_exists[path]:missing.append(Path(path).name)
            item['images_checked']=record.get('images_extracted',False)
            item['missing_files']=missing;item['storage_unavailable']=not storage_available
        counts={'available':0,'review':0,'completed':0}
        for item in items:counts['completed' if item['key'] in known else 'available']+=1
        resumable=any(i['key'] not in known for i in self.get('queue',[]))
        from app.mmf_release_prepare import preparation_status, release_lifecycle, release_collages
        prepared=preparation_status(self)
        return {'availability_claimed':bool(checked and self.get('download_checked_at',0)==checked),'collages':release_collages(items,self.store.config()['download_directory']),'release_lifecycle':release_lifecycle(items,prepared),'preparations':prepared,'resumable':resumable,'connected':self.session.exists() and not self.get('auth_required',False),'settings':settings,'creators':self.get('creators',[]),'active':active,'job':job,'counts':counts,'checked_at':checked,'next_check_at':max(checked,attempt)+interval if checked or attempt else 0,'interval_hours':interval/3600,'items':[{**i,'completed':i['key'] in known} for i in items],'folders':self.store.config_view()['folders']}
    def login(self,payload):
        with self.idle():
            groups=self.client().login(payload.get('username'),payload.get('password'))
            self.put('creators',[{'id':int(g['id']),'name':g['name']} for g in groups]);self.put('checked_at',0);self.put('auth_required',False)
        return self.state()
    def refresh_creators(self):
        if not self.session.exists():raise LoginRequired('Connect your MMF account first.')
        # Independent of the transfer lock: discovery reads metadata only and
        # must never replace subscriptions, availability or the running queue.
        with (self.directory/'creators.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            creators=self.client().groups()
            self.put('creators',creators)
        return {'creators':creators}
    def save(self,payload):
        with self.idle():
            previous=self.get('settings',{'revision':0,'subscriptions':[]})
            if payload.get('revision')!=previous['revision']:raise FileExistsError('MMF settings changed in another tab. Reload first.')
            subs=payload.get('subscriptions');creators={str(g['id']):g for g in self.get('creators',[])}
            if not isinstance(subs,list) or len(subs)>1000:raise MMFError('Invalid subscriptions.')
            rows=[];seen=set();base=Path(self.store.config()['download_directory']).resolve()
            for sub in subs:
                if not isinstance(sub,dict):raise MMFError('Invalid subscription.')
                cid=str(sub.get('id'));folder=sub.get('folder');start=sub.get('start_month','')
                if cid not in creators or cid in seen:raise MMFError('Select each MMF creator only once.')
                if folder is None or isinstance(folder,str) and not folder.strip():folder=default_creator_folder(creators[cid]['name'])
                if not isinstance(folder,str) or not folder or folder in ('.','..') or re.search(r'[\\/<>:"|?*\x00-\x1f]',folder) or folder.strip()!=folder or (base/folder).is_symlink():raise MMFError('Choose a creator folder directly inside the download folder.')
                if not isinstance(start,str) or (start and not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',start)):raise MMFError('Choose a starting month or All (archiving).')
                seen.add(cid);rows.append({'id':int(cid),'name':creators[cid]['name'],'folder':folder,'start_month':start})
            self.put('settings',{'revision':previous['revision']+1,'subscriptions':rows});self.put('checked_at',0);self.put('items',[]);self.put('queue',[]);self.put('attempted_at',0)
        return self.state()
    def start(self,action,due=False,release=None):
        if action not in ('check','download','resume','redownload'):raise MMFError('Unknown MMF task.')
        lock=(self.directory/'worker.lock').open('a')
        try:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise FileExistsError('An MMF task is already running.')
            if not self.session.exists():raise LoginRequired('Connect your MMF account first.')
            if due and (self.get('auth_required',False) or time.time()<max(self.get('checked_at',0),self.get('attempted_at',0))+self.store.config()['mmf_availability_interval_hours']*3600):return {'started':False}
            settings=self.get('settings',{})
            if not settings.get('subscriptions') and action!='check':return {'started':False}
            if action=='download':
                if not self.get('checked_at',0):raise MMFError('Check availability before starting downloads.')
                known=self.completed();groups=releases(self.get('items',[]),self.store.config()['download_directory']);queue=[i for group in groups.values() if any(i['key'] not in known for i in group) for i in group]
                if not queue:raise MMFError('No files are available in the selected scope.')
                self.put('redownload',{})
                self.put('download_checked_at',self.get('checked_at',0));self.put('queue',queue);self.put('run_base',self.store.config()['download_directory'])
            if action=='redownload':
                if not isinstance(release,str) or not re.fullmatch(r'[a-f0-9]{64}',release):raise MMFError('Choose a valid release to re-download.')
                from app.mmf_release_prepare import downloaded
                items=self.get('items',[]);current={(i['creator_id'],i['object_id'],i['archive_id']) for i in items}
                items.extend(i for i in downloaded(self) if (i['creator_id'],i['object_id'],i['archive_id']) not in current)
                group=releases(items,self.store.config()['download_directory']).get(release,[])
                subscribed={str(s['id']):s for s in settings['subscriptions']}
                if not group or any(str(i['creator_id']) not in subscribed for i in group):raise MMFError('This release is not available for a subscribed artist.')
                for item in group:item['folder']=subscribed[str(item['creator_id'])]['folder']
                releases(group,self.store.config()['download_directory'])
                run={'id':os.urandom(16).hex(),'keys':[i['key'] for i in group],'release_key':release}
                with self.db() as db:previous=[json.loads(value) for key,value in db.execute('SELECT key,data FROM completed') if key in run['keys']]
                self.put('redownload_history:'+run['id'],previous)
                self.put('redownload',run);self.put('queue',group);self.put('run_base',self.store.config()['download_directory'])
            if action=='resume' and not self.get('queue',[]):raise MMFError('No saved queue to resume.')
            if action=='check':self.put('attempted_at',time.time())
            self.put('stop',False);self.put('job',{'phase':'starting','action':action,'message':'Starting MMF task…','errors':[]})
            log=os.open(self.directory/'worker.log',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            try:subprocess.Popen([sys.executable,'-m','app.mmf_manager',str(self.root),action,str(lock.fileno())],pass_fds=(lock.fileno(),),stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,cwd=self.root)
            finally:os.close(log)
        finally:lock.close()
        return {'started':True}
    def stop(self):self.put('stop',True);return {'stopping':True}
    def stopped(self):return self.get('stop',False)
    def progress(self,**values):
        state=self.get('job',{});state.update(values);self.put('job',state)
    def check(self):
        client=self.client();groups=client.groups();self.put('creators',[{'id':int(g['id']),'name':g['name']} for g in groups])
        tribe_creators={str(g['id']) for g in groups if 'TRIBE' in g.get('sources',[])}
        objects=getattr(client,'library_objects',None)
        if objects is None:objects=client.metadata('/api/data-library/objectPreviews')
        if not isinstance(objects,list):raise MMFError('Unexpected MMF library response.')
        subs={str(s['id']):s for s in self.get('settings',{}).get('subscriptions',[])};overrides=self.get('months',{});items=[]
        labels={};metadata_keys=set()
        selected=[o for o in objects if str(o.get('creatorId')) in subs and o.get('source') in ('USER_GROUP','TRIBE','FRONTIER') and o.get('type')=='object']
        for obj in selected:
            if self.stopped():raise MMFError('Check stopped. Previous availability results retained.')
            source=obj['source'];cid=str(obj['creatorId']);owner=str(obj.get('campaignId')) if source=='FRONTIER' else cid
            key=(source,owner)
            if key in metadata_keys:continue
            metadata_keys.add(key)
            if not owner.isdecimal():raise MMFError('MMF returned an invalid release owner.')
            endpoint={'USER_GROUP':'userGroup','TRIBE':'tribe','FRONTIER':'frontier'}[source]
            remote_releases=client.metadata('/api/data-library/'+endpoint+'_releases_metadata/'+owner)
            if source=='FRONTIER':
                if not isinstance(remote_releases,dict):raise MMFError('Unexpected MMF Frontier releases response.')
                remote_releases=[*remote_releases.get('pledges',[]),*remote_releases.get('addons',[]),*([remote_releases['signup']] if remote_releases.get('signup') else [])]
            if not isinstance(remote_releases,list):raise MMFError('Unexpected MMF releases response.')
            for release in remote_releases:labels[(source,owner,str(release['id']))]=release
        # Keep every entitlement until scope filtering: an old shared entry
        # must not hide the same model in a current Tribe release.
        selected.sort(key=lambda o:({'USER_GROUP':0,'FRONTIER':1,'TRIBE':2}[o['source']],str(o.get('release',''))))
        downloadables={}
        for number,obj in enumerate(selected,1):
            if self.stopped():raise MMFError('Check stopped. Previous availability results retained.')
            source=obj['source'];cid=str(obj['creatorId']);owner=str(obj.get('campaignId')) if source=='FRONTIER' else cid
            sub=subs[cid];release=labels.get((source,owner,str(obj.get('release'))),{});label=release.get('label') or release.get('name') or ''
            self.progress(phase='checking',message=f"Checking {sub['name']} · {obj['name']}",done=number-1,total=len(selected))
            oid=int(obj['originalId'])
            if oid not in downloadables:downloadables[oid]=client.downloadables(oid)['archives']
            for archive in downloadables[oid]:
                size=int(archive['size']);filename=archive['name']
                if size<=0 or not isinstance(filename,str):continue
                item={'object_id':int(obj['originalId']),'archive_id':int(archive['id']),'size':size,'filename':filename,'updated_at':archive.get('updatedAt',''),'creator':sub['name'],'creator_id':sub['id'],'folder':sub['folder'],'object_name':obj['name'],'release':label,'release_created_at':release.get('createdAt'),'release_id':str(obj['release']) if obj.get('release') is not None else None,'month':month_for(filename,label),'start_month':sub['start_month']}
                item['library_source']=source
                if source!='USER_GROUP':
                    item['release_id']=f'{source}:{owner}:{obj.get("release",obj["originalId"])}'
                    # Tribe delivery may be backed by a campaign-tier entitlement
                    # with no release metadata. Model creation can predate that
                    # subscription delivery by months; do not use it as its date.
                    item['release_created_at']=None if source=='FRONTIER' else release.get('createdAt') or obj.get('libraryAddedAt') or obj.get('publishedAt') or obj.get('createdAt')
                    if source=='TRIBE' and not release.get('createdAt') and obj.get('libraryAddedAt'):item['release_date_basis']='delivery_at'
                item['key']=version_key(item);item['month']=overrides.get(item['key'],item['month'])
                items.append(item)
        groups=releases(items,self.store.config()['download_directory'])
        from app.mmf_release_prepare import downloaded
        records=downloaded(self);historical={i['release_key'] for i in records}
        included=[i for group in groups.values() if group[0]['release_key'] in historical or any(not i['start_month'] or not i['release_month'] or i['release_month']>=i['start_month'] for i in group) for i in group]
        excluded=len(groups)-len({i['release_key'] for i in included})
        # Prefer a previously recorded destination, including for revised
        # archive versions, then a deterministic source. Fetch each file once.
        recorded_releases={(i['object_id'],i['archive_id']):i['release_key'] for i in records}
        def source_priority(item):
            # Actual Tribe memberships outrank campaign aliases. Some Frontier
            # purchases also emit synthetic TRIBE previews without membership.
            if item['library_source']=='TRIBE' and str(item['creator_id']) in tribe_creators:return -1
            return {'USER_GROUP':0,'FRONTIER':1,'TRIBE':2}[item['library_source']]
        included.sort(key=lambda i:(recorded_releases.get((i['object_id'],i['archive_id']))!=i['release_key'],source_priority(i),i['release_key']))
        unique={}
        for item in included:unique.setdefault(item['key'],item)
        included=list(unique.values())
        self.put('items',included);self.put('checked_at',time.time());client.save()
        count=len({release_key(i) for i in included})
        message=f'Check complete · {count} releases in scope.'
        if excluded:message+=f' {excluded} older releases excluded by the starting month. Select All (archiving) to include them.'
        if not subs:message='Artist list refreshed from Shared with me, Tribes and Frontiers. Select artists to check their files.'
        self.progress(phase='complete',message=message,done=len(selected),total=len(selected))
    def deliver_originals(self,group,paths,units,base,identity,nonce):
        first=group[0];release_folder=first['release_folder'];records=[];staged_images=[]
        def moving(n,total):
            if self.stopped():raise MMFError('Re-download stopped. Resume to finish saving archives.')
            self.progress(phase='moving',message='Saving original archives',bytes=n,expected=total,speed_mbps=0)
        with release_lock(self.root,base,first['folder'],release_folder,self.stopped):
            for unit in units.values():
                unit.sort(key=lambda i:volume_key(i['filename'])[1]);head=unit[0]
                unit_id=hashlib.sha256((identity+str(head['object_id'])+head['filename']).encode()).hexdigest()
                # Separate source sets to preserve original names, including multipart archives.
                sub=('MMF sources','originals',unit_id[:16]);outputs=[]
                for item in unit:
                    path=paths[item['key']]
                    dest,size,checksum=deliver(path,base,first['folder'],release_folder,path.name,moving,subdirectories=sub,release_directory=release_folder)
                    outputs.append({'path':str(dest),'size':size,'sha256':checksum})
                warnings=[]
                def extraction(phase,count,total,received,expected):
                    self.progress(phase='extracting',progress_stage=phase,image_count=count,image_total=total,
                                  message='Extracting images · '+head['filename'],bytes=received or 0,expected=expected or 0,
                                  progress_unit='percent' if phase=='checking_parts' else 'bytes',speed_mbps=0)
                images=extract_images(paths[head['key']],self.directory/'staging'/identity/'images'/unit_id,extraction,self.stopped,warnings=warnings)
                staged_images.extend(images);image_records=[]
                for index,image in enumerate(images):
                    dest,size,checksum=deliver(image,base,first['folder'],release_folder,f'{unit_id[:8]}-{index+1}--{image.name}',moving,subdirectories=('release_images',),release_directory=release_folder)
                    image_records.append({'path':str(dest),'size':size,'sha256':checksum})
                manifest={'id':unit_id,'kind':'original_archives','outputs':outputs,'images':image_records,
                          'source_keys':[i['key'] for i in unit],'release_key':release_key(first),
                          'release_name':head['object_name'],'release_folder':release_folder}
                for item,output in zip(unit,outputs):
                    records.append({**item,'destination':output['path'],'sha256':output['sha256'],'repack':manifest,
                                    'images_extracted':True,'image_count':len(image_records) if item is head else 0,'warnings':warnings,
                                    'completed_at':time.time(),'redownload_id':nonce})
            with self.db() as db:
                for record in records:db.execute('INSERT OR REPLACE INTO completed VALUES (?,?)',(record['key'],json.dumps(record)))
        for path in [*paths.values(),*staged_images]:
            if path.exists():path.unlink()

    def download(self):
        client=self.client();queue=self.get('queue',[]);known=self.completed()-self.pending_redownload_keys();base=self.get('run_base');errors=[];run_warnings=[];done=sum(i['key'] in known for i in queue)
        groups=releases(queue,base)
        for group in groups.values():
            remaining=[i for i in group if i['key'] not in known]
            if not remaining:continue
            if self.stopped():break
            group.sort(key=lambda i:(i['object_id'],volume_key(i['filename'])[0],volume_key(i['filename'])[1]));first=group[0]
            self.progress(release_key=release_key(first))
            release_folder=first['release_folder']
            redownload=self.get('redownload',{})
            nonce=redownload.get('id','') if any(i['key'] in redownload.get('keys',[]) for i in group) else ''
            identity=hashlib.sha256((json.dumps(sorted(i['key'] for i in group))+nonce).encode()).hexdigest()
            work=self.directory/'staging'/identity;work.mkdir(parents=True,exist_ok=True)
            try:
                current={};paths={};units={};names=set()
                for item in group:units.setdefault((item['object_id'],volume_key(item['filename'])[0]),[]).append(item)
                for item in group:
                    name=safe_component(item['filename']);unit_key=(item['object_id'],volume_key(item['filename'])[0]);unit_dir=work/('source-'+hashlib.sha256(json.dumps(unit_key).encode()).hexdigest()[:16]);unit_dir.mkdir(exist_ok=True)
                    name_key=(unit_key,name.casefold())
                    if name_key in names:raise MMFError('Two MMF files have the same staged filename; review this release.')
                    names.add(name_key);paths[item['key']]=unit_dir/name
                    if item['object_id'] not in current:current[item['object_id']]={int(a['id']):a for a in client.downloadables(item['object_id'])['archives']}
                    remote=current[item['object_id']].get(item['archive_id'])
                    if not remote or int(remote['size'])!=item['size'] or remote.get('updatedAt','')!=item['updated_at']:raise MMFError('MMF file changed after checking. Run Check availability again.')
                    self.progress(phase='downloading',message=item['filename'],done=done,total=len(queue),bytes=0,expected=item['size'],speed_mbps=0)
                    last=[0.0]
                    def transfer(n,total,speed):
                        if time.monotonic()-last[0]>.35 or n==total:self.progress(bytes=n,expected=total,speed_mbps=round(speed,2));last[0]=time.monotonic()
                    client.download(item,paths[item['key']],transfer,self.stopped)
                self.deliver_originals(group,paths,units,base,identity,nonce)
                known.update(i['key'] for i in group);done+=len(remaining)
                self.progress(done=done,total=len(queue))
            except LoginRequired:raise
            except Exception as error:
                errors.append({'creator':first['creator'],'release':first['release'],'error':str(error) if isinstance(error,(ValueError,OSError,ExtractionError,DeliveryError)) else type(error).__name__})
                self.progress(errors=errors)
        phase='stopped' if self.stopped() else 'error' if errors or done<len(queue) else 'complete'
        message=('Download error — run incomplete. ' if phase=='error' else '')+f'{done}/{len(queue)} files completed.'
        if done<len(queue):message+=' Saved progress is retained. Restore the connection if needed, then Resume to retry unfinished files.'
        self.progress(phase=phase,message=message,done=done,total=len(queue),errors=errors,bytes=0,expected=0,speed_mbps=0)
        client.save()

if __name__=='__main__':
    from app.subscription_store import SubscriptionStore
    os.umask(0o077)
    manager=MMFManager(SubscriptionStore(Path(sys.argv[1])))
    try:
        manager.check() if sys.argv[2]=='check' else manager.download()
    except Exception as error:
        if isinstance(error,LoginRequired):manager.put('auth_required',True)
        manager.progress(phase='error',message=str(error) if isinstance(error,(ValueError,OSError)) else 'MMF task failed; saved progress retained.',speed_mbps=0)
    finally:os.close(int(sys.argv[3]))
