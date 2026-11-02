"""Manual release uploads: every retry is a new durable attempt, collage first."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from app.file_delivery import digest, release_lock
from app.telegram_cli import TelegramCLI, CLIError, require_release_collage

ACTIVE={'queued','uploading','verifying','preparing','conversation'}


def queue_lock(manager):return manager.root/'data'/'release-upload-queue.lock'
def worker_lock(manager):return manager.root/'data'/'release-upload-worker.lock'


def managers(manager):
    """Both providers share a Telegram connection and must serialize conversations."""
    result=[manager]
    other='gumroad' if manager.provider=='mmf' else 'mmf'
    if (manager.root/'data'/other/'manager.sqlite3').exists():
        from app.mmf_manager import MMFManager
        from app.gumroad_manager import GumroadManager
        result.append((GumroadManager if other=='gumroad' else MMFManager)(manager.store))
    return result


def pending(manager):
    return sorted(((m,r) for m in managers(manager) for r in attempts(m) if r['state']=='queued'),
                  key=lambda pair:(pair[1]['created_at'],pair[1]['id']))


def attempts(manager):
    with manager.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS release_uploads (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        return [json.loads(r[0]) for r in db.execute('SELECT data FROM release_uploads')]


def save(manager,row):
    with manager.db() as db:
        db.execute('INSERT OR REPLACE INTO release_uploads VALUES (?,?)',(row['id'],json.dumps(row)))
        if row.get('parent_upload_id'):
            found=db.execute('SELECT data FROM release_uploads WHERE id=?',(row['parent_upload_id'],)).fetchone()
            if found:
                parent=json.loads(found[0])
                for key in ('files','bytes','total','speed_mbps','current'):parent[key]=row.get(key)
                parent['message']='Release channel: '+row.get('message','')
                parent['state']='uploading' if row['state'] in ACTIVE else 'preparing'
                db.execute('UPDATE release_uploads SET data=? WHERE id=?',(json.dumps(parent),parent['id']))


def busy(manager):
    with worker_lock(manager).open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);return False
        except BlockingIOError:return True


def completion_ready(row):
    files=row.get('files',[]);messages=row.get('messages',[])
    return (row.get('delivery',{}).get('mode')=='bot' and row.get('delivery',{}).get('profile')=='eve'
            and len(files)>=2 and len(files)==len(messages)
            and all(f.get('uploaded')==f.get('size') and m.get('message_id')
                    for f,m in zip(files,messages)))


def pad_source(manager,plan_id):
    from app.mmf_release_prepare import rows
    plan=next((p for p in rows(manager) if p['id']==plan_id),{})
    expected={Path(o['path']).name:o for o in plan.get('outputs',[])}
    def matches_plan(row):
        archives=[f for f in row.get('files',[]) if f.get('name') in expected]
        return len(archives)==len(expected) and all(
            ('size' not in expected[f['name']] or f.get('size')==expected[f['name']]['size']) and
            ('sha256' not in expected[f['name']] or f.get('sha256')==expected[f['name']]['sha256']) for f in archives)
    candidates=[r for r in attempts(manager) if r['preparation_id']==plan_id and r['state']=='complete'
                and r.get('delivery',{}).get('mode','pad')=='pad' and matches_plan(r)]
    if not candidates:return None
    source=max(candidates,key=lambda r:r['created_at'])
    files=source.get('files',[]);messages=source.get('messages',[])
    if (len(files)<2 or len(files)!=len(messages)
            or not re.fullmatch(r'-[1-9][0-9]{0,18}',source.get('destination',''))
            or any(f.get('state')!='complete' or not m.get('message_id') or
                   m.get('kind')!=('collage' if n==0 else 'archive')
                   for n,(f,m) in enumerate(zip(files,messages)))):
        raise ValueError('The saved release-channel receipt is incomplete. Review it before releasing; no files were re-uploaded.')
    return source


def status(manager):
    with queue_lock(manager).open('a') as gate, worker_lock(manager).open('a') as lock:
        fcntl.flock(gate,fcntl.LOCK_EX)
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);active=False
        except BlockingIOError:active=True
        rows=attempts(manager)
        for row in rows:
            if row['state'] in ACTIVE-{'queued'} and not active:
                row.update(state='interrupted',message='Upload interrupted. Check the selected destination before re-uploading; some messages may already exist.')
                save(manager,row)
        positions={r['id']:n+1 for n,(m,r) in enumerate(pending(manager))}
        for row in rows:
            row['can_finish']=row['state'] in ('failed','interrupted') and completion_ready(row)
            if row['state']=='queued':
                row['queue_position']=positions[row['id']]
                row['message']=f"Queued · position {row['queue_position']}" if active else 'Upload queue paused. Resume when ready.'
        return {'active':active,'attempts':rows}


def inputs(manager,plan):
    base=Path(manager.store.config()['download_directory']).resolve(strict=True)
    folder=Path(plan['directory'])
    if not folder.resolve(strict=True).is_relative_to(base):raise ValueError('This release is outside the configured download folder.')
    archives=[]
    for output in plan.get('outputs',[]):
        p=Path(output['path'])
        if p.parent!=folder or not re.search(r'\.7z(?:\.\d{3,})?$',p.name,re.I) or not p.is_file():raise ValueError('A prepared release archive is missing.')
        if any(part.is_symlink() for part in (p,*p.parents)):raise ValueError('Release paths must not be symbolic links.')
        if not 0<p.stat().st_size<=4000*1024*1024:raise ValueError('Release archive size is invalid.')
        if output.get('size') is not None and p.stat().st_size!=output['size']:raise ValueError('The release archive differs from its prepared version. Restore or rebuild it before uploading.')
        archives.append(p)
    if not archives:raise ValueError('Prepare this release before uploading.')
    collage=require_release_collage(archives[0])
    return collage,archives


def start(manager,payload):
    with queue_lock(manager).open('a') as gate:
        fcntl.flock(gate,fcntl.LOCK_EX)
        return enqueue(manager,payload)


def enqueue(manager,payload):
    from app.mmf_release_prepare import rows
    lock=worker_lock(manager).open('a')
    try:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);active=False
        except BlockingIOError:active=True
        if payload.get('retry_completion'):
            row=next((r for r in attempts(manager) if r['id']==payload['retry_completion']),None)
            if not row or row['state'] not in ('failed','interrupted') or not completion_ready(row):
                raise ValueError('Only a fully delivered bot release can retry completion.')
            row.update(state='queued',completion_only=True,message='Queued to finish the existing bot release.')
            for f in row['files']:f['state']='complete'
            save(manager,row)
            if not active:launch(manager,row,lock)
            return {'started':True,'queued':active,'attempt_id':row['id']}
        if payload.get('cancel_queued'):
            row=next((r for r in attempts(manager) if r['id']==payload['cancel_queued']),None)
            if not row or row['state']!='queued':raise ValueError('Only waiting uploads can be removed from the queue.')
            row.update(state='cancelled',message='Removed from upload queue.');save(manager,row)
            return {'cancelled':True}
        if payload.get('resume_queue'):
            if active:return {'started':True}
            waiting=pending(manager)
            if not waiting:raise ValueError('No waiting uploads to resume.')
            launch(manager,waiting[0][1],lock)
            return {'started':True}
        workflow=payload.get('workflow')
        if workflow:
            if not isinstance(workflow,dict) or not isinstance(workflow.get('keys'),list) or not re.fullmatch(r'[a-f0-9]{64}',str(workflow.get('release_key',''))):raise ValueError('Invalid release workflow.')
            plan={'id':None,'title':payload['title']}
        else:
            plan=next((p for p in rows(manager) if p['id']==payload.get('preparation_id') and p['state']=='complete'),None)
            if not plan:raise ValueError('Choose a prepared release.')
            if payload.get('delivery',{}).get('mode')!='bot' or not pad_source(manager,plan['id']):inputs(manager,plan)
        choice=payload.get('delivery') or {'mode':'pad','destination':manager.store.config().get('release_pad_destination','')}
        destination=choice.get('destination','')
        if choice.get('mode')=='bot':
            if not pad_source(manager,plan['id']) and not re.fullmatch(r'-[1-9][0-9]{0,18}',manager.store.config().get('release_pad_destination','')):
                raise ValueError('Choose Your Telegram Release Channel in Configuration before releasing through a bot.')
            from app.mmf_release_flow import delivery
            checked=delivery(manager,{**choice,'bot':destination,**{k:'\n'.join(choice[k]) for k in ('before','after') if isinstance(choice.get(k),list)}})
            choice={**choice,**checked}
        elif choice.get('mode')!='pad' or not re.fullmatch(r'-[1-9][0-9]{0,18}',destination):raise ValueError('Choose Your Telegram Release Channel in Configuration.')
        history=attempts(manager)
        if any(r['state'] in ACTIVE and ((workflow and r.get('workflow',{}).get('release_key')==workflow['release_key']) or (plan['id'] and r['preparation_id']==plan['id'])) for r in history):
            raise FileExistsError('This release is already uploading or queued.')
        row={'id':uuid.uuid4().hex,'preparation_id':plan['id'],'title':plan['title'],'destination':destination,'delivery':choice,'state':'queued','message':'Preparing upload.','created_at':time.time(),'messages':[],
             'number':1+bool(plan.get('test_upload'))+sum(r['preparation_id']==plan['id'] for r in history)}
        if workflow:row['workflow']=workflow
        save(manager,row)
        if not active:launch(manager,row,lock)
        return {'started':True,'queued':active,'attempt_id':row['id']}
    finally:lock.close()


def launch(manager,row,lock):
    log=os.open(manager.directory/'upload.log',os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
    try:
        subprocess.Popen([sys.executable,'-m','app.mmf_release_upload',str(manager.root),row['id'],str(lock.fileno()),getattr(manager,'provider','mmf')],pass_fds=(lock.fileno(),),stdout=log,stderr=log,stdin=subprocess.DEVNULL,start_new_session=True,cwd=manager.root)
    except Exception:
        raise RuntimeError('Could not start upload worker. The saved queue can be resumed.') from None
    finally:os.close(log)


def drain(manager,lock_fd):
    while True:
        with queue_lock(manager).open('a') as gate:
            fcntl.flock(gate,fcntl.LOCK_EX)
            waiting=pending(manager)
            if not waiting:
                # Release ownership under the enqueue gate: no lost wakeup.
                fcntl.flock(lock_fd,fcntl.LOCK_UN)
                return
            owner,row=waiting[0]
            row.update(state='preparing',message='Preparing upload.');save(owner,row)
        try:
            if row.get('workflow') and not row.get('preparation_id'):
                from app import mmf_release_prepare as prepare
                current=[i for i in owner.state()['items'] if i['release_key']==row['workflow']['release_key']]
                if sorted({i['key'] for i in current})!=row['workflow']['keys'] or any(not i['completed'] for i in current):raise ValueError('Release files changed. Review the release before queuing again.')
                try:result=prepare.start(owner,{'release_key':row['workflow']['release_key']},synchronous=True)
                except FileExistsError:
                    if not owner.busy():raise
                    with queue_lock(manager).open('a') as gate:
                        fcntl.flock(gate,fcntl.LOCK_EX)
                        row.update(state='queued',message='Waiting for the current preparation or download.');save(owner,row)
                    time.sleep(1)
                    continue
                row['preparation_id']=result['preparation_id'];save(owner,row)
            execute(owner,row)
        except Exception as error:
            row.update(state='failed',message=str(error)+' Upload queue paused; review this attempt before retrying.')
            save(owner,row)
            return


def execute(manager,row,client=None):
    from app.mmf_release_prepare import rows
    plan=next(p for p in rows(manager) if p['id']==row['preparation_id'])
    if row.get('delivery',{}).get('mode')=='bot' and not row.get('completion_only') and not pad_source(manager,plan['id']):
        target=manager.store.config().get('release_pad_destination','')
        if not re.fullmatch(r'-[1-9][0-9]{0,18}',target):raise ValueError('Choose Your Telegram Release Channel in Configuration before releasing through a bot.')
        pad={'id':uuid.uuid4().hex,'preparation_id':plan['id'],'title':plan['title'],'destination':target,
             'delivery':{'mode':'pad','destination':target},'state':'preparing','message':'Uploading to release channel first.',
             'created_at':time.time(),'messages':[],'number':row['number'],'parent_upload_id':row['id']}
        save(manager,pad)
        execute(manager,pad,client)
    client=client or TelegramCLI(root=manager.root)
    work=manager.directory/'uploads'/row['id'];work.mkdir(parents=True,exist_ok=True,mode=0o700)
    def update(state,message):
        if state=='complete' and row.get('cleanup_warning'):message+=' Source cleanup needs attention: '+row['cleanup_warning']
        row.update(state=state,message=message);save(manager,row)
    def export(label):
        output=work/(label+'.json')
        client._run(['stl','release-history','--chat',row['destination'],'--output',output],work,lambda:False,timeout=120)
        data=json.loads(output.read_text())
        if row.get('bot_id'):
            raw_id=row['bot_id'];kind='UserID'
        else:
            marked=int(row['destination']);raw_id=-marked-1000000000000 if marked<=-1000000000000 else -marked
            kind='ChannelID' if marked<=-1000000000000 else 'ChatID'
        if data.get('id')!=raw_id or any(m.get('raw',{}).get('PeerID')!={kind:raw_id} for m in data['messages']):raise CLIError('Upload destination verification failed.')
        return data['messages']
    try:
        update('preparing','Checking destination and collage.')
        conversation=None
        if row.get('delivery',{}).get('mode')=='bot':
            from app.mmf_release_flow import BotConversation
            conversation=BotConversation(client,work,{**row['delivery'],'title':plan['title']},export,update)
            conversation.command('info')
            peer=json.loads((work/'bot.json').read_text())
            if peer.get('bot') is not True or type(peer.get('id')) is not int or peer['id']<=0:raise CLIError('The recipient is not a verified bot.')
            row['bot_id']=peer['id'];row['destination']=str(peer['id']);conversation.choice['destination']=row['destination']
        else:
            allowed=client.destinations()['destinations']
            peer=next((d for d in allowed if d['id']==row['destination']),None)
            if not peer:raise CLIError('The saved Release Pad is unavailable or does not allow uploads.')
        row['destination_title']=peer['title']
        if row.get('completion_only'):
            if not conversation or not completion_ready(row):raise ValueError('No verified bot delivery to finish.')
            history=export('completion-recovery')
            first=min(m['message_id'] for m in row['messages']);last=max(m['message_id'] for m in row['messages'])
            if any(m['id']>last and (m['raw'].get('Out') or 'Upload a release for ' in m['raw'].get('Message','')) for m in history):
                raise ValueError('The bot conversation has changed since this release. Review it before completing; no files were resent.')
            conversation.baseline={m['id']:conversation.fingerprint(m) for m in history if m['id']<first}
            confirmed=next((m for m in history if m['id']>last and not m['raw'].get('Out') and 'Upload done and saved.' in m['raw'].get('Message','')),None)
            confirmation=confirmed or conversation.finish()
            record_publication(manager,row,confirmation)
            row['finished_at']=time.time();update('complete','Release published and confirmed by the bot.')
            return
        source=pad_source(manager,plan['id']) if conversation else None
        if conversation and not source:raise ValueError('A verified release-channel upload is required before forwarding to the bot.')
        if source:
            forward_existing(manager,row,source,conversation,client,work,export,update)
            row['finished_at']=time.time()
            update('complete','Release published and confirmed by the bot.')
            return
        with release_lock(manager.root,plan['base'],plan['folder'],plan['release_folder']):
            collage,archives=inputs(manager,plan)
            sources=[collage,*archives]
            if shutil.disk_usage(work).free<sum(p.stat().st_size for p in sources)+64*1024*1024:raise ValueError('Not enough local space to stage the release upload.')
            staged=[]
            # Snapshot the current release files, including deliberate local replacements.
            for source in sources:
                target=work/source.name;shutil.copyfile(source,target)
                checksum=digest(target)
                if source.stat().st_size!=target.stat().st_size or digest(source)!=checksum:raise ValueError('Release changed while staging. Retry when editing is finished.')
                expected=next((o for o in plan.get('outputs',[]) if Path(o['path'])==source),{})
                if expected.get('sha256') and checksum!=expected['sha256']:raise ValueError('The release archive checksum differs from its prepared version. Restore or rebuild it before uploading.')
                staged.append({'path':str(source),'name':source.name,'size':target.stat().st_size,'sha256':checksum,'uploaded':0,'state':'queued'})
            row['files']=staged;row['total']=sum(f['size'] for f in staged);row['bytes']=0;save(manager,row)
        if conversation:conversation.begin()
        for info in staged:
            info['name']=conversation.upload_filename(info['name']) if conversation else info['name']
        for index,info in enumerate(staged):
            if conversation:conversation.checkpoint()
            path=work/Path(info['path']).name
            before={m['id'] for m in export(f'before-{index}')}
            update('uploading',f'Uploading {index+1}/{len(staged)}: {path.name}')
            # The app's archive gate expects the artist/month folder structure.
            send_dir=work/'send'/Path(plan['folder'])/Path(plan['release_folder']);send_dir.mkdir(parents=True,exist_ok=True)
            sent_path=send_dir/info['name']
            if not sent_path.exists():os.link(path,sent_path)
            if index and not (send_dir/collage.name).exists():os.link(work/collage.name,send_dir/collage.name)
            if index:require_release_collage(sent_path)
            progress_file=work/(f'progress-{index}.json')
            args=['stl','release-upload','--chat',row['destination'],'--path',sent_path,'--caption',plan['title'],'--progress',progress_file]
            if index==0:args.append('--photo')
            info['state']='uploading';row['current']=index;row['speed_mbps']=0
            def tick():
                try:progress=json.loads(progress_file.read_text())
                except (OSError,ValueError):return
                info['uploaded']=max(info['uploaded'],min(info['size'],int(progress.get('uploaded',0))))
                row['bytes']=sum(f['uploaded'] for f in staged)
                row['speed_mbps']=round(max(0,float(progress.get('speed_mbps',0))),2)
                save(manager,row)
            client._run(args,work,lambda:False,tick=tick,timeout=3600)
            tick();info['state']='verifying';row['speed_mbps']=0
            update('verifying',f'Verifying {path.name} at the selected destination.')
            matches=[]
            for m in export(f'after-{index}'):
                raw=m['raw'];media=raw.get('Media') or {}
                if m['id'] in before or not raw.get('Out') or raw.get('Message')!=plan['title']:continue
                if index==0 and media.get('Photo'):matches.append(m)
                if index and m.get('file')==sent_path.name and (media.get('Document') or {}).get('Size')==info['size']:matches.append(m)
            if len(matches)!=1:raise CLIError('Could not verify delivery. Check the selected destination before re-uploading; the message may already exist.')
            info.update(uploaded=info['size'],state='complete');row['bytes']=sum(f['uploaded'] for f in staged)
            row['messages'].append({'message_id':matches[0]['id'],'filename':sent_path.name,'kind':'collage' if index==0 else 'archive'});save(manager,row)
        if conversation:
            first=min(m['message_id'] for m in row['messages'])
            conversation.baseline={k:v for k,v in conversation.baseline.items() if k<first}
            confirmation=conversation.finish()
            record_publication(manager,row,confirmation)
        row['finished_at']=time.time();update('complete','Release published and confirmed by the bot.' if conversation else 'Release uploaded and verified.')
    except Exception as error:
        row['speed_mbps']=0
        if 'current' in row and row['files'][row['current']]['state']!='complete':row['files'][row['current']]['state']='failed'
        row['finished_at']=time.time();update('failed',str(error)+' Re-upload is available.');raise
    finally:
        client.close()
        # Only private upload snapshots; release files on the destination are untouched.
        for path in work.glob('*'):
            if path.is_file() and re.search(r'\.(?:jpg|7z)(?:\.\d+)?$',path.name,re.I):path.unlink()
        if (work/'send').is_dir():shutil.rmtree(work/'send')


def forward_existing(manager,row,source,conversation,client,work,export,update):
    """Reuse verified Telegram messages; never stage or upload local bytes."""
    from app.mmf_release_prepare import rows,save as save_plan
    row['source_upload_id']=source['id'];row['transfer_mode']='forward'
    row['files']=[{**f,'uploaded':0,'state':'queued'} for f in source['files']]
    row['total']=sum(f['size'] for f in row['files']);row['bytes']=0;row['speed_mbps']=0
    save(manager,row)
    conversation.begin()
    for index,(info,message) in enumerate(zip(row['files'],source['messages'])):
        conversation.checkpoint()
        before={m['id'] for m in export(f'before-forward-{index}')}
        row['current']=index;info['state']='forwarding'
        # Persist a stable request ID before sending, including on uncertain delivery.
        info['random_id']=uuid.uuid4().int & ((1<<63)-1) or 1
        update('uploading',f'Forwarding {index+1}/{len(row["files"])} from your release channel: {info["name"]}')
        client._run(['stl','release-forward','--from',source['destination'],'--to',row['destination'],
                     '--message',message['message_id'],'--random-id',info['random_id'],
                     '--output',work/f'forward-{index}.json'],work,lambda:False,timeout=120)
        matches=[]
        for m in export(f'after-forward-{index}'):
            raw=m['raw'];media=raw.get('Media') or {}
            # The CLI serializes absent TL headers as an object of zero values.
            # Only populated fields indicate retained forwarding attribution.
            attribution=raw.get('FwdFrom') or {}
            if m['id'] in before or not raw.get('Out') or any(attribution.values()):continue
            if raw.get('Message')!=source['title']:continue
            if index==0 and media.get('Photo'):matches.append(m)
            elif index and m.get('file')==message['filename'] and (media.get('Document') or {}).get('Size')==info['size']:matches.append(m)
        if len(matches)!=1:raise CLIError('Could not verify the forwarded message. Check the bot before retrying. No local files were uploaded.')
        info.update(uploaded=info['size'],state='complete');row['bytes']=sum(f['uploaded'] for f in row['files'])
        row['messages'].append({**message,'message_id':matches[0]['id']});save(manager,row)
    first=min(m['message_id'] for m in row['messages'])
    conversation.baseline={k:v for k,v in conversation.baseline.items() if k<first}
    confirmation=conversation.finish()
    record_publication(manager,row,confirmation)


def record_publication(manager,row,confirmation):
    from app.mmf_release_prepare import rows,save as save_plan,cleanup_published_sources
    plan=next(p for p in rows(manager) if p['id']==row['preparation_id'])
    plan['publication']={'confirmed_at':time.time(),'method':'bot_confirmation','upload_id':row['id'],
                         'message_id':confirmation['id'],'keys':list(plan['keys'])}
    save_plan(manager,plan)
    cleanup_published_sources(manager,plan)
    if plan.get('source_cleanup',{}).get('state')=='warning':row['cleanup_warning']=plan['source_cleanup']['message']


if __name__=='__main__':
    from app.subscription_store import SubscriptionStore
    from app.mmf_manager import MMFManager
    if len(sys.argv)>4 and sys.argv[4]=='gumroad':
        from app.gumroad_manager import GumroadManager
        manager=GumroadManager(SubscriptionStore(Path(sys.argv[1])))
    else:manager=MMFManager(SubscriptionStore(Path(sys.argv[1])))
    try:drain(manager,int(sys.argv[3]))
    finally:os.close(int(sys.argv[3]))
