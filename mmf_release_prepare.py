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
from file_delivery import deliver, digest, release_lock
from mmf_repack import repack, archive_name, VOLUME_BYTES, COMPRESSION_LEVEL, POLICY_VERSION
from mmf_images import fetch_images
from release_images import safe_component, volume_key


def rows(manager):
    with manager.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS telegram_preparations (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        return [json.loads(r[0]) for r in db.execute('SELECT data FROM telegram_preparations')]


def save(manager, plan):
    with manager.db() as db:
        db.execute('INSERT OR REPLACE INTO telegram_preparations VALUES (?,?)',(plan['id'],json.dumps(plan)))


def downloaded(manager):
    from mmf_manager import releases
    with manager.db() as db:items=[json.loads(r[0]) for r in db.execute('SELECT data FROM completed')]
    latest={}
    for item in sorted(items,key=lambda i:i.get('completed_at',0)):
        latest[(item['creator_id'],item['object_id'],item['archive_id'])]=item
    items=list(latest.values());releases(items)
    return items


def preparation_status(manager):
    from mmf_release_upload import status
    uploads=status(manager)
    result={}
    for plan in sorted(rows(manager),key=lambda p:p['number']):
        key=plan['release_key'];summary=result.setdefault(key,{'number':-1,'keys':[],'published_keys':[]})
        summary['upload_active']=uploads['active']
        if plan['state']=='complete':
            history=sorted((r for r in uploads['attempts'] if r['preparation_id']==plan['id']),key=lambda r:r['created_at'])
            last=history[-1] if history else plan.get('test_upload')
            summary.setdefault('packages',[]).append({'id':plan['id'],'title':plan['title'],'published':bool(plan.get('publication')),'upload':({k:last.get(k) for k in ('id','state','message','bytes','total','speed_mbps','current')}|{'files':[{k:f.get(k) for k in ('name','size','uploaded','state')} for f in last.get('files',[])]}) if last else None,'attempted':bool(last),'upload_state':last.get('state') if last else None,'upload_message':last.get('message','Test upload '+last.get('state','')) if last else ''})
            summary.update(number=plan['number'],title=plan['title'],directory=plan['directory'])
            summary['keys']=sorted(set(summary['keys'])|set(plan['keys']))
            summary['published_keys']=sorted(set(summary['published_keys'])|set(plan.get('publication',{}).get('keys',[])))
        else:summary['pending']=plan['title']
    return result


def confirm_released(manager, payload):
    """Record a user's explicit publication confirmation, never just an upload."""
    from mmf_release_upload import attempts
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


def start(manager,payload):
    key=payload.get('release_key')
    if not isinstance(key,str):raise ValueError('Choose a downloaded monthly release.')
    lock=(manager.directory/'worker.lock').open('a')
    try:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise FileExistsError('Wait for the current MMF task to finish.')
        history=rows(manager);group=[i for i in downloaded(manager) if i['release_key']==key]
        if not group or not group[0].get('release_month'):raise ValueError('Choose a downloaded monthly release.')
        # Do not silently issue a partial month while known files await downloading.
        current=[i for i in manager.state()['items'] if i['release_key']==key]
        if any(not i['completed'] for i in current):raise ValueError('Download the available files for this month first.')
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
        manager.put('stop',False);manager.put('job',{'phase':'starting','action':'prepare','message':'Preparing '+plan['title'],'errors':[]})
        log=os.open(manager.directory/'worker.log',os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
        try:Popen([sys.executable,str(manager.root/'mmf_release_prepare.py'),str(manager.root),plan['id'],str(lock.fileno())],pass_fds=(lock.fileno(),),stdout=log,stderr=log,stdin=DEVNULL,start_new_session=True,cwd=manager.root)
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
    work=manager.directory/'release-preparation'/plan['id'];work.mkdir(parents=True,exist_ok=True)
    def progress(phase,percent):
        if manager.stopped():raise ValueError('Preparation stopped. Use the same preparation button to resume.')
        manager.progress(phase=phase,message=plan['title'],bytes=percent,expected=100)
    with release_lock(manager.root,plan['base'],plan['folder'],plan['release_folder'],manager.stopped):
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
            if len(manifests)==1 and manifest.get('compression_level')==COMPRESSION_LEVEL and manifest.get('policy_version')==POLICY_VERSION and set(manifest.get('source_keys',[]))==set(plan['keys']) and all(re.search(r'\.7z(?:\.\d{3,})?$',p.name,re.I) and p.stat().st_size<=VOLUME_BYTES for p in outputs):
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
            packed=repack(sources[0]['path'],work/'packed',plan['id'],progress,manager.stopped,sources=sources,release_name=plan['title'])
        fallback=[] if images else fetch_images(manager.client(),plan['items'],work/'mmf-images',manager.stopped,progress)
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
        month=plan['items'][0]['release_month'];artist=plan['folder'].lstrip('!- ')
        collage=Path(plan['base'])/plan['folder']/plan['release_folder']/(artist+'-'+month+'.jpg')
        has_collage=collage.is_file() and not collage.is_symlink()
        if has_collage:deliver(collage,plan['base'],plan['folder'],plan['release_folder'],collage.name,moving,subdirectories=sub,release_directory=plan['release_folder'])
        plan.update(state='complete',compression_level=COMPRESSION_LEVEL,outputs=outputs,directory=str(Path(plan['base'])/plan['folder']/plan['release_folder']/Path(*sub)),completed_at=time.time(),collage_included=has_collage)
        save(manager,plan)
        manager.progress(phase='complete',message=plan['title']+' prepared'+('' if has_collage else ' · no saved collage found'),bytes=100,expected=100)


if __name__=='__main__':
    from mmf_manager import MMFManager
    from subscription_store import SubscriptionStore
    manager=MMFManager(SubscriptionStore(Path(sys.argv[1])))
    plan=next(p for p in rows(manager) if p['id']==sys.argv[2])
    try:execute(manager,plan)
    except Exception as error:
        plan.update(state='failed',error=str(error));save(manager,plan)
        manager.progress(phase='error',message=str(error),speed_mbps=0)
    finally:os.close(int(sys.argv[3]))
