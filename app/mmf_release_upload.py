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


def attempts(manager):
    with manager.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS release_uploads (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        return [json.loads(r[0]) for r in db.execute('SELECT data FROM release_uploads')]


def save(manager,row):
    with manager.db() as db:
        db.execute('INSERT OR REPLACE INTO release_uploads VALUES (?,?)',(row['id'],json.dumps(row)))


def busy(manager):
    with (manager.directory/'upload.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);return False
        except BlockingIOError:return True


def status(manager):
    with (manager.directory/'upload.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);active=False
        except BlockingIOError:active=True
        rows=attempts(manager)
        for row in rows:
            if row['state'] in ACTIVE and not active:
                row.update(state='interrupted',message='Upload interrupted. Check the selected destination before re-uploading; some messages may already exist.')
                save(manager,row)
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
        archives.append(p)
    if not archives:raise ValueError('Prepare this release before uploading.')
    collage=require_release_collage(archives[0])
    return collage,archives


def start(manager,payload):
    from app.mmf_release_prepare import rows
    lock=(manager.directory/'upload.lock').open('a')
    try:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise FileExistsError('A release upload is already running.')
        plan=next((p for p in rows(manager) if p['id']==payload.get('preparation_id') and p['state']=='complete'),None)
        if not plan:raise ValueError('Choose a prepared release.')
        inputs(manager,plan)
        choice=payload.get('delivery') or {'mode':'pad','destination':manager.store.config().get('release_pad_destination','')}
        destination=choice.get('destination','')
        if choice.get('mode')=='bot':
            from app.mmf_release_flow import delivery
            checked=delivery(manager,{**choice,'bot':destination,**{k:'\n'.join(choice[k]) for k in ('before','after') if isinstance(choice.get(k),list)}})
            choice={**choice,**checked}
        elif choice.get('mode')!='pad' or not re.fullmatch(r'-[1-9][0-9]{0,18}',destination):raise ValueError('Choose Your Telegram Release Channel in Configuration.')
        history=attempts(manager)
        row={'id':uuid.uuid4().hex,'preparation_id':plan['id'],'title':plan['title'],'destination':destination,'delivery':choice,'state':'queued','message':'Preparing upload.','created_at':time.time(),'messages':[],
             'number':1+bool(plan.get('test_upload'))+sum(r['preparation_id']==plan['id'] for r in history)}
        save(manager,row)
        log=os.open(manager.directory/'upload.log',os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
        try:
            subprocess.Popen([sys.executable,'-m','app.mmf_release_upload',str(manager.root),row['id'],str(lock.fileno())],pass_fds=(lock.fileno(),),stdout=log,stderr=log,stdin=subprocess.DEVNULL,start_new_session=True,cwd=manager.root)
        except Exception:
            row.update(state='failed',message='Could not start upload. Re-upload is available.');save(manager,row);raise
        finally:os.close(log)
        return {'started':True,'attempt_id':row['id']}
    finally:lock.close()


def execute(manager,row,client=None):
    from app.mmf_release_prepare import rows
    plan=next(p for p in rows(manager) if p['id']==row['preparation_id'])
    client=client or TelegramCLI(root=manager.root)
    work=manager.directory/'uploads'/row['id'];work.mkdir(parents=True,exist_ok=True,mode=0o700)
    def update(state,message):row.update(state=state,message=message);save(manager,row)
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
                staged.append({'path':str(source),'name':source.name,'size':target.stat().st_size,'sha256':checksum,'uploaded':0,'state':'queued'})
            row['files']=staged;row['total']=sum(f['size'] for f in staged);row['bytes']=0;save(manager,row)
        if conversation:conversation.begin()
        for index,info in enumerate(staged):
            if conversation:conversation.checkpoint()
            path=work/Path(info['path']).name
            before={m['id'] for m in export(f'before-{index}')}
            update('uploading',f'Uploading {index+1}/{len(staged)}: {path.name}')
            # The app's archive gate expects the artist/month folder structure.
            send_dir=work/'send'/Path(plan['folder'])/Path(plan['release_folder']);send_dir.mkdir(parents=True,exist_ok=True)
            sent_path=send_dir/path.name
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
                if index and m.get('file')==path.name and (media.get('Document') or {}).get('Size')==info['size']:matches.append(m)
            if len(matches)!=1:raise CLIError('Could not verify delivery. Check the selected destination before re-uploading; the message may already exist.')
            info.update(uploaded=info['size'],state='complete');row['bytes']=sum(f['uploaded'] for f in staged)
            row['messages'].append({'message_id':matches[0]['id'],'filename':path.name,'kind':'collage' if index==0 else 'archive'});save(manager,row)
        if conversation:
            confirmation=conversation.finish()
            from app.mmf_release_prepare import save as save_plan
            plan['publication']={'confirmed_at':time.time(),'method':'bot_confirmation','upload_id':row['id'],'message_id':confirmation['id'],'keys':list(plan['keys'])}
            save_plan(manager,plan)
        row['finished_at']=time.time();update('complete','Release published and confirmed by the bot.' if conversation else 'Release uploaded and verified.')
    except Exception as error:
        row['speed_mbps']=0
        if 'current' in row:row['files'][row['current']]['state']='failed'
        row['finished_at']=time.time();update('failed',str(error)+' Re-upload is available.');raise
    finally:
        client.close()
        # Only private upload snapshots; release files on the destination are untouched.
        for path in work.glob('*'):
            if path.is_file() and re.search(r'\.(?:jpg|7z)(?:\.\d+)?$',path.name,re.I):path.unlink()
        if (work/'send').is_dir():shutil.rmtree(work/'send')


if __name__=='__main__':
    from app.subscription_store import SubscriptionStore
    from app.mmf_manager import MMFManager
    manager=MMFManager(SubscriptionStore(Path(sys.argv[1])))
    try:execute(manager,next(r for r in attempts(manager) if r['id']==sys.argv[2]))
    finally:os.close(int(sys.argv[3]))
