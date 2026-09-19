"""Manual, durable Telegram release packages from verified MMF downloads."""
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
from subprocess import Popen, DEVNULL
import sys
import time
from app.file_delivery import deliver, digest, release_lock
from app.mmf_repack import repack, archive_name, POLICY_VERSION
from app.mmf_images import fetch_images
from app.release_images import safe_component, volume_key
from app.telegram_cli import require_release_collage, CLIError


def rows(manager):
    with manager.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS telegram_preparations (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        return [json.loads(r[0]) for r in db.execute('SELECT data FROM telegram_preparations')]


def save(manager, plan):
    with manager.db() as db:
        db.execute('INSERT OR REPLACE INTO telegram_preparations VALUES (?,?)',(plan['id'],json.dumps(plan)))


def downloaded(manager):
    from app.mmf_manager import releases
    with manager.db() as db:items=[json.loads(r[0]) for r in db.execute('SELECT data FROM completed')]
    latest={}
    for item in sorted(items,key=lambda i:i.get('completed_at',0)):
        latest[(item['creator_id'],item['object_id'],item['archive_id'])]=item
    items=list(latest.values());releases(items)
    return items


def preparation_status(manager):
    from app.mmf_release_upload import status
    uploads=status(manager)
    result={}
    for plan in sorted(rows(manager),key=lambda p:p['number']):
        key=plan['release_key'];summary=result.setdefault(key,{'number':-1,'keys':[],'published_keys':[]})
        summary['upload_active']=uploads['active']
        if plan['state']=='complete':
            if plan.get('manual_finish'):
                summary['number']=max(summary['number'],plan['number'])
                summary['keys']=sorted(set(summary['keys'])|set(plan['keys']))
                summary['published_keys']=sorted(set(summary['published_keys'])|set(plan['keys']))
                continue
            history=sorted((r for r in uploads['attempts'] if r['preparation_id']==plan['id']),key=lambda r:r['created_at'])
            last=history[-1] if history else plan.get('test_upload')
            summary.setdefault('packages',[]).append({'id':plan['id'],'title':plan['title'],'published':bool(plan.get('publication')),'upload':({k:last.get(k) for k in ('id','state','message','bytes','total','speed_mbps','current')}|{'files':[{k:f.get(k) for k in ('name','size','uploaded','state')} for f in last.get('files',[])]}) if last else None,'attempted':bool(last),'upload_state':last.get('state') if last else None,'upload_message':last.get('message','Test upload '+last.get('state','')) if last else ''})
            summary.update(number=plan['number'],title=plan['title'],directory=plan['directory'])
            summary['keys']=sorted(set(summary['keys'])|set(plan['keys']))
            summary['published_keys']=sorted(set(summary['published_keys'])|set(plan.get('publication',{}).get('keys',[])))
        else:summary['pending']=plan['title']
    return result


def finish_manually(manager, payload):
    """Record external publication of the exact versions the user reviewed."""
    from app.mmf_release_upload import status
    if payload.get('confirmed') is not True:raise ValueError('Confirm that this release was finished outside the tool.')
    with manager.idle():
        if status(manager)['active']:raise FileExistsError('Wait for the current release upload to finish.')
        key=payload.get('release_key')
        group=[i for i in manager.state()['items'] if i['release_key']==key]
        if not group:raise ValueError('Choose an existing release.')
        keys=sorted({i['key'] for i in group})
        if payload.get('keys')!=keys:raise FileExistsError('Release files changed. Review the release and try again.')
        history=[p for p in rows(manager) if p['release_key']==key]
        published={k for p in history for k in p.get('publication',{}).get('keys',[])}
        if set(keys)<=published and all(p.get('publication') for p in history):return {'recorded':True}
        number=max((p['number'] for p in history),default=-1)
        if not any(not p.get('publication') for p in history):number+=1
        number=max(0,number)
        publication={'confirmed_at':time.time(),'method':'manual_external','keys':keys}
        plan={'id':'manual-'+hashlib.sha256((key+json.dumps(keys)).encode()).hexdigest(),
              'release_key':key,'number':number,'title':group[0]['release_folder'],
              'keys':keys,'state':'complete','manual_finish':True,'publication':publication}
        with manager.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for previous in history:
                if previous.get('publication'):continue
                previous['publication']={**publication,'keys':list(previous['keys'])}
                if previous['state']!='complete':previous.update(state='complete',manual_finish=True)
                db.execute('UPDATE telegram_preparations SET data=? WHERE id=?',(json.dumps(previous),previous['id']))
            db.execute('INSERT OR REPLACE INTO telegram_preparations VALUES (?,?)',(plan['id'],json.dumps(plan)))
        return {'recorded':True}


def confirm_released(manager, payload):
    """Record a user's explicit publication confirmation, never just an upload."""
    from app.mmf_release_upload import attempts
    if payload.get('confirmed') is not True:
        raise ValueError('Confirm only after the release bot has successfully finished publishing.')
    history=attempts(manager)
    with manager.db() as db:
        db.execute('BEGIN IMMEDIATE')
        record=db.execute('SELECT data FROM telegram_preparations WHERE id=?',
                          (payload.get('preparation_id'),)).fetchone()
        if not record:raise ValueError('Choose a prepared release.')
        plan=json.loads(record[0])
        if plan.get('publication'):return {'recorded':True}
        upload=next((u for u in history if u['id']==payload.get('attempt_id')
                     and u['preparation_id']==plan['id'] and u['state']=='complete'),None)
        if plan['state']!='complete' or not upload:
            raise ValueError('A verified successful upload is required before confirming publication.')
        # A replaced/test archive must not mark the original source versions as published.
        expected={(Path(f['path']).name,f.get('size'),f.get('sha256')) for f in plan.get('outputs',[])}
        actual={(f['name'],f.get('size'),f.get('sha256')) for f in upload.get('files',[])[1:]}
        if not expected or expected!=actual or any(not f[2] for f in expected):
            raise ValueError('Uploaded archives differ from the prepared release. Upload the real prepared archives first.')
        plan['publication']={'confirmed_at':time.time(),'method':'user_confirmation',
                             'upload_id':upload['id'],'keys':list(plan['keys'])}
        db.execute('UPDATE telegram_preparations SET data=? WHERE id=?',(json.dumps(plan),plan['id']))
    return {'recorded':True}


def has_release_images(directory, depth=0):
    """Look for nonempty editor-supported images; do not follow symbolic links."""
    from app.collages import SUPPORTED, BYTE_LIMIT
    if depth>8 or directory.is_symlink():return False
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():continue
                if entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in SUPPORTED:
                    if 0<entry.stat(follow_symlinks=False).st_size<=BYTE_LIMIT:return True
                elif entry.is_dir(follow_symlinks=False) and has_release_images(Path(entry.path),depth+1):
                    return True
    except OSError:pass
    return False


def release_collages(items, base):
    from app.collages import collage_filename, valid_release_folder
    result={}
    for item in items:
        key=item['release_key']
        if key in result:continue
        folder=item.get('folder');release=item.get('release_folder')
        if not valid_release_folder(folder) or not valid_release_folder(release):continue
        directory=Path(base)/folder/release
        try:
            if (Path(base)/folder).is_symlink() or directory.is_symlink():continue
            collage=directory/collage_filename({'folder':folder,'month':release})
            images=directory/'release_images'
            try:require_release_collage(directory/'release.7z',action='making the release');valid=True
            except CLIError:valid=False
            result[key]={'valid':valid,'exists':collage.is_file() and not collage.is_symlink(),
                         'ready':has_release_images(images),
                         'folder':folder,'release':release}
        except OSError:continue
    return result


def release_lifecycle(items, prepared):
    """Exact source versions reopen a published month when old releases change."""
    result={}
    for item in items:
        key=item['release_key'];summary=prepared.get(key,{})
        state=result.setdefault(key,{'finished':True,'published':bool(summary.get('published_keys')),'new_files':0})
        if item['key'] not in summary.get('published_keys',[]):
            state['finished']=False;state['new_files']+=1
    for key,state in result.items():
        if any(not p.get('published') for p in prepared.get(key,{}).get('packages',[])):
            state['finished']=False
    return result


def start_images(manager, payload,from_mmf=False):
    key=payload.get('release_key')
    group=[i for i in downloaded(manager) if i['release_key']==key]
    if not group:raise ValueError('Download this release first.')
    lock=(manager.directory/'worker.lock').open('a')
    try:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise FileExistsError('Wait for the current MMF task to finish.')
        manager.put('stop',False)
        manager.put('job',{'phase':'starting','action':'prepare_images','release_key':key,'message':'Preparing release images','errors':[]})
        log=os.open(manager.directory/'worker.log',os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
        try:Popen([sys.executable,'-m','app.mmf_release_prepare',str(manager.root),('gallery:' if from_mmf else 'images:')+key,str(lock.fileno())],pass_fds=(lock.fileno(),),stdout=log,stderr=log,stdin=DEVNULL,start_new_session=True,cwd=manager.root)
        finally:os.close(log)
    finally:lock.close()
    return {'started':True}


def prepare_images(manager, key,from_mmf=False):
    from app.release_images import extract_images
    group=[i for i in downloaded(manager) if i['release_key']==key]
    if not group:raise ValueError('Downloaded release is missing.')
    first=group[0];base=manager.store.config()['download_directory']
    work=manager.directory/'release-images'/key;work.mkdir(parents=True,exist_ok=True)
    records=[];warnings=[];seen=set()
    previous=manager.get('prepared_images:'+key,{})
    if set(previous.get('keys',[]))=={i['key'] for i in group} and previous.get('images'):
        try:
            for record in previous['images']:verified(record['path'],record,base)
        except (OSError,ValueError):pass
        else:
            manager.progress(phase='complete',message='Images ready. Create and save a collage next.',speed_mbps=0);return
    def progress(stage='verifying_images',count=0,total=None,received=0,expected=None):
        if manager.stopped():raise ValueError('Image preparation stopped.')
        labels={'verifying_images':'Verifying existing images','listing':'Reading archive contents','checking_parts':'Checking archive parts','extracting':'Extracting images','recovering':'Recovering images','fetching_images':'Fetching MMF images','moving_images':'Saving release images','complete':'Images extracted'}
        manager.progress(phase='extracting',progress_stage=stage,progress_unit='percent' if stage=='checking_parts' else 'bytes',
                         message=labels.get(stage,'Preparing images')+' · '+first['release_folder'],
                         image_count=count,image_total=total,done=count,total=total or 0,bytes=received or 0,expected=expected or 0,speed_mbps=0)
    with release_lock(manager.root,base,first['folder'],first['release_folder'],manager.stopped):
        for item in group:
            manifest=item.get('repack') or {};mid=manifest.get('id')
            if not mid or mid in seen:continue
            seen.add(mid);progress()
            existing=manifest.get('images',[])
            try:
                for record in existing:verified(record['path'],record,base)
            except (OSError,ValueError):existing=[]
            if existing:records.extend(existing);continue
            outputs=manifest.get('outputs',[])
            images=[]
            if outputs and not from_mmf:
                sources=[verified(r['path'],r,base) for r in outputs]
                images=extract_images(sorted(sources)[0],work/mid,progress,manager.stopped,warnings=warnings)
            if not images and from_mmf:
                images=fetch_images(manager.client(),[i for i in group if (i.get('repack') or {}).get('id')==mid],work/(mid+'-gallery'),manager.stopped,progress)
            for index,image in enumerate(images):
                progress('moving_images',index,len(images))
                dest,size,checksum=deliver(image,base,first['folder'],first['release_folder'],mid[:8]+'-'+str(index)+'--'+image.name,lambda n,total:progress('moving_images',index,len(images),n,total),subdirectories=('release_images',),release_directory=first['release_folder'])
                records.append({'path':str(dest),'size':size,'sha256':checksum})
        manager.put('prepared_images:'+key,{'images':records,'keys':[i['key'] for i in group]})
        manager.progress(phase='complete',message=('Images ready. Create and save a collage next.' if records else 'No images found in MMF galleries.' if from_mmf else 'No images found in the archives. You can download images from MMF.'),warnings=warnings,speed_mbps=0)


def start(manager,payload):
    key=payload.get('release_key')
    if not isinstance(key,str):raise ValueError('Choose a downloaded monthly release.')
    lock=(manager.directory/'worker.lock').open('a')
    try:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise FileExistsError('Wait for the current MMF task to finish.')
        history=rows(manager);group=[i for i in downloaded(manager) if i['release_key']==key]
        if not group:raise ValueError('Choose a downloaded release.')
        directory=Path(manager.store.config()['download_directory'])/group[0]['folder']/group[0]['release_folder']
        require_release_collage(directory/'release.7z',action='making the release')
        # Do not silently issue a partial release while known files await downloading.
        current=[i for i in manager.state()['items'] if i['release_key']==key]
        if any(not i['completed'] for i in current):raise ValueError('Download the available files for this release first.')
        pending=[p for p in history if p['release_key']==key and p['state']!='complete']
        if pending:plan=pending[0]
        else:
            completed=[p for p in history if p['release_key']==key and p['state']=='complete']
            issued={k for p in completed for k in p['keys']}
            delta=[i for i in group if i['key'] not in issued]
            if not delta:raise ValueError('No new or updated downloaded files to prepare.')
            number=max((p['number'] for p in completed),default=-1)+1
            title=group[0]['release_folder']+(f' Addendum {number}' if number else '')
            plan={'id':hashlib.sha256((key+':'+str(number)).encode()).hexdigest(),'release_key':key,'number':number,'title':title,'keys':[i['key'] for i in delta],'items':delta,'state':'pending','base':manager.store.config()['download_directory'],'folder':group[0]['folder'],'release_folder':group[0]['release_folder']}
            save(manager,plan)
        config=manager.store.config()
        plan['delivery']=payload.get('delivery')
        plan.setdefault('compression_level',config.get('release_compression_level',7))
        plan.setdefault('volume_bytes',config.get('release_volume_mib',4000)*1024*1024)
        save(manager,plan)
        manager.put('stop',False);manager.put('job',{'phase':'starting','action':'package','release_key':key,'message':'Making '+plan['title'],'errors':[]})
        log=os.open(manager.directory/'worker.log',os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
        try:Popen([sys.executable,'-m','app.mmf_release_prepare',str(manager.root),plan['id'],str(lock.fileno())],pass_fds=(lock.fileno(),),stdout=log,stderr=log,stdin=DEVNULL,start_new_session=True,cwd=manager.root)
        finally:os.close(log)
    finally:lock.close()
    return {'started':True}


def verified(path,receipt,base):
    path=Path(path)
    if path.is_symlink() or not path.resolve(strict=True).is_relative_to(Path(base).resolve(strict=True)):
        raise ValueError('Prepared input is outside the configured download folder.')
    if path.stat().st_size!=receipt['size'] or digest(path)!=receipt['sha256']:
        raise ValueError('Downloaded input changed or is incomplete: '+path.name)
    return path


def execute(manager,plan):
    config=manager.store.config()
    compression=plan.get('compression_level',config.get('release_compression_level',7))
    volume_bytes=plan.get('volume_bytes',config.get('release_volume_mib',4000)*1024*1024)
    work=manager.directory/'release-preparation'/plan['id'];work.mkdir(parents=True,exist_ok=True)
    def progress(phase,percent):
        if manager.stopped():raise ValueError('Preparation stopped. Use the same preparation button to resume.')
        manager.progress(phase=phase,message=plan['title'],bytes=percent,expected=100)
    with release_lock(manager.root,plan['base'],plan['folder'],plan['release_folder'],manager.stopped):
        release_archive=Path(plan['base'])/plan['folder']/plan['release_folder']/'release.7z'
        require_release_collage(release_archive,action='making the release')
        manifests={};selected={}
        for item in plan['items']:
            manifest=item.get('repack')
            if not manifest:raise ValueError('This download has no verified repack receipt. Download it again before preparing.')
            manifests[manifest['id']]=manifest;selected.setdefault(manifest['id'],[]).append(item)
        sources=[];images={};reusable=None
        for mid,manifest in manifests.items():
            progress('verifying_inputs',0)
            outputs=[verified(r['path'],r,plan['base']) for r in manifest['outputs']]
            outputs.sort();prefixes=manifest.get('source_prefixes',{})
            for r in manifest.get('images',[]):images[r['path']]=r
            if manifest.get('kind')=='original_archives':
                head=selected[mid][0]
                sources.append({'path':outputs[0],'folder':safe_component(head['object_name'])+'/'+archive_name(head['filename'])[:-3]+'-'+mid[:8]})
                continue
            if len(manifests)==1 and manifest.get('compression_level')==compression and manifest.get('policy_version')==POLICY_VERSION and set(manifest.get('source_keys',[]))==set(plan['keys']) and all(re.search(r'\.7z(?:\.\d{3,})?$',p.name,re.I) and p.stat().st_size<=volume_bytes for p in outputs):
                reusable=outputs
            include=[]
            for item in selected[mid]:
                # Legacy downloader used this same object/archive directory layout.
                prefix=prefixes.get(item['key']) or safe_component(item['object_name'])+'/'+archive_name(item['filename'])[:-3]
                include.append(prefix)
            sources.append({'path':outputs[0],'folder':safe_component(manifest.get('release_name') or 'Release')+'-'+mid[:8] if len(manifests)>1 else '', 'include_prefixes':sorted(set(include))})
            for r in manifest.get('images',[]):images[r['path']]=r
        if reusable:
            packed=reusable
            manager.progress(phase='moving',message='Reusing verified archive · '+plan['title'],bytes=0,expected=0)
        else:
            packed=repack(sources[0]['path'],work/'packed',plan['id'],progress,manager.stopped,compression_level=compression,volume_bytes=volume_bytes,sources=sources,release_name=plan['title'])
        for record in manager.get('prepared_images:'+plan['release_key'],{}).get('images',[]):
            images.setdefault(record['path'],record)
        fallback=[]
        sub=();outputs=[]
        def moving(n,total):
            if manager.stopped():raise ValueError('Preparation stopped. Use the preparation button to resume.')
            manager.progress(phase='moving',message=plan['title'],bytes=n,expected=total)
        for index,path in enumerate(packed,1):
            if manager.stopped():raise ValueError('Preparation stopped. Use the preparation button to resume.')
            dest,size,checksum=deliver(path,plan['base'],plan['folder'],plan['release_folder'],plan['title']+'.7z'+(f'.{index:03}' if len(packed)>1 else ''),moving,subdirectories=sub,release_directory=plan['release_folder']);outputs.append({'path':str(dest),'size':size,'sha256':checksum})
        for r in images.values():
            path=verified(r['path'],r,plan['base'])
            deliver(path,plan['base'],plan['folder'],plan['release_folder'],path.name,moving,subdirectories=(*sub,'release_images'),release_directory=plan['release_folder'])
        for path in fallback:
            deliver(path,plan['base'],plan['folder'],plan['release_folder'],path.name,moving,subdirectories=('release_images',),release_directory=plan['release_folder'])
        require_release_collage(release_archive,action='making the release')
        plan.update(state='complete',compression_level=compression,volume_bytes=volume_bytes,outputs=outputs,directory=str(Path(plan['base'])/plan['folder']/plan['release_folder']/Path(*sub)),completed_at=time.time(),collage_included=True)
        save(manager,plan)
        manager.progress(phase='complete',message=plan['title']+' prepared',bytes=100,expected=100)


if __name__=='__main__':
    from app.mmf_manager import MMFManager
    from app.subscription_store import SubscriptionStore
    manager=MMFManager(SubscriptionStore(Path(sys.argv[1])))
    plan=None
    try:
        if sys.argv[2].startswith('gallery:'):prepare_images(manager,sys.argv[2][8:],from_mmf=True)
        elif sys.argv[2].startswith('images:'):prepare_images(manager,sys.argv[2][7:])
        else:
            plan=next(p for p in rows(manager) if p['id']==sys.argv[2])
            execute(manager,plan)
    except Exception as error:
        if plan and plan.get('state')!='complete':
            plan.update(state='failed',error=str(error));save(manager,plan)
        manager.progress(phase='error',message=str(error),speed_mbps=0)
    finally:
        try:
            if plan and plan.get('state')=='complete' and plan.get('delivery') and not manager.stopped():
                try:
                    from app.mmf_release_upload import start as upload
                    upload(manager,{'preparation_id':plan['id'],'delivery':plan['delivery']})
                except Exception as error:
                    manager.progress(phase='error',message='Archive ready, but release delivery could not start: '+str(error),speed_mbps=0)
        finally:os.close(int(sys.argv[3]))
