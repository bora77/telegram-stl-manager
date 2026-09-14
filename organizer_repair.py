"""Explicit downloads of missing organizer parts, with retained local originals."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from file_delivery import deliver, digest
from telegram_cli import TelegramCLI, safe_filename
from transfer_metrics import TransferMeter


def candidates(job, plan):
    if not job or not job.get('plan_id') or job['plan_id']!=plan.get('id') or job['topic_url']!=plan.get('topic_url') or plan.get('backend')!='cli':return []
    result=[]
    for group in job['groups']:
        if group['state']=='completed':continue
        names={r['filename'] for r in group['records']}
        for part in group.get('part_requirements',[]):
            name=part['filename']
            if name in names or not safe_filename(name) or len(part['sizes'])!=1:continue
            items=[a for a in plan.get('attachments',[]) if a['filename']==name and a.get('bytes_total')==part['sizes'][0]]
            items=[a for a in items if a.get('topic_url')==job['topic_url'] and not a.get('unsafe_filename')
                   and all(type(a.get(k)) is int and a[k]>0 for k in ('source_message_id','bytes_total','dc_id'))
                   and type(a.get('document_id')) is int and a['document_id']!=0
                   and a.get('message_url')==job['topic_url']+'/'+str(a['source_message_id'])]
            originals=[f for f in plan.get('files',[]) if f['filename']==name]
            if not items or len(originals)>1:continue
            original=originals[0] if originals else None
            if original and (original.get('telegram_status')!='size_mismatch' or not original.get('identity')):continue
            result.append({'group':group['key'],'item':max(items,key=lambda a:a['source_message_id']),
                           'original':{'source':original['source'],'identity':original['identity']} if original else None})
    return result


def prepare(job, plan, scope):
    if scope.topic_id(job['topic_url']) is None:raise ValueError('Repair is outside the approved source.')
    options=candidates(job,plan)
    if not options:raise ValueError('No unambiguous missing or incomplete parts are available. Run a fresh preview.')
    for option in options:
        group=next(g for g in job['groups'] if g['key']==option['group'])
        repairs=group.setdefault('repairs',[])
        if not any(r['item']['filename']==option['item']['filename'] for r in repairs):repairs.append(option)


def remove_original(worker, pinned, repair):
    original=repair['original'];relative=original['source'];expected=original['identity']
    current=pinned.info(relative)
    if current is None and repair.get('removal_started'):return
    if current!=expected:raise ValueError('Incomplete original changed since preview; run a new preview.')
    backup=repair['backup'];path=Path(backup['path'])
    if path.is_symlink() or path.stat().st_size!=expected['size'] or digest(path)!=backup['sha256']:
        raise ValueError('Local backup failed verification; original retained.')
    worker.check();repair['removal_started']=True;worker.save()
    fd,name=pinned.parent(relative)
    try:
        from organizer_apply import identity
        if identity(os.stat(name,dir_fd=fd,follow_symlinks=False))!=expected:raise ValueError('Incomplete original changed before replacement.')
        os.unlink(name,dir_fd=fd);os.fsync(fd)
    finally:os.close(fd)


def backup_original(worker, pinned, repair, directory):
    original=repair['original']
    if not original:return
    target=directory/'previous'/Path(original['source']).name
    target.parent.mkdir(parents=True,exist_ok=True)
    expected=original['identity'];saved=repair.get('backup')
    if saved:
        if target.is_symlink() or target.stat().st_size!=expected['size'] or digest(target)!=saved['sha256']:
            raise ValueError('Saved local backup needs review; no original was removed.')
        return
    if pinned.info(original['source'])!=expected:raise ValueError('Incomplete original changed since preview; run a new preview.')
    if shutil.disk_usage(directory).free<expected['size']+1024**3:raise ValueError('Not enough local space to preserve the incomplete original.')
    from organizer_apply import identity
    fd,name=pinned.parent(original['source'])
    temporary=None
    try:
        incoming_fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
        with os.fdopen(incoming_fd,'rb') as incoming,tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as output:
            temporary=Path(output.name);checksum=hashlib.sha256();done=0;meter=TransferMeter()
            if identity(os.fstat(incoming.fileno()))!=expected:raise ValueError('Incomplete original changed before backup.')
            while block:=incoming.read(4*1024*1024):
                worker.check();done+=len(block)
                if done>expected['size']:raise ValueError('Incomplete original grew during backup.')
                output.write(block);checksum.update(block);metrics=meter.sample(done)
                worker.progress('repair_backup','Keeping a local backup · '+name,done,expected['size'],speed_bps=metrics['download_speed_bps'])
            output.flush();os.fsync(output.fileno())
            if done!=expected['size'] or identity(os.fstat(incoming.fileno()))!=expected or pinned.info(original['source'])!=expected:
                raise ValueError('Incomplete original changed during backup.')
        if digest(temporary)!=checksum.hexdigest():raise ValueError('Local backup checksum mismatch; original retained.')
        if target.exists():
            if target.is_symlink() or digest(target)!=checksum.hexdigest():raise ValueError('A different local backup already exists.')
        else:os.link(temporary,target)
        fd_backup=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd_backup)
        finally:os.close(fd_backup)
        repair['backup']={'path':str(target),'sha256':checksum.hexdigest()};worker.save()
    finally:
        os.close(fd)
        if temporary:temporary.unlink(missing_ok=True)


def run(worker, group, pinned):
    for repair in group.get('repairs',[]):
        if repair.get('installed'):continue
        item=repair['item'];name=item['filename'];original=repair['original']
        if worker.store.history.source.topic_id(item['topic_url']) is None or item['topic_url']!=worker.job['topic_url']:
            raise ValueError('Repair is outside the approved creator.')
        if original and not repair.get('removal_started') and pinned.info(original['source'])!=original['identity']:
            raise ValueError('Incomplete original changed since preview; run a new preview.')
        row=worker.store.history.register(source_message_id=item['source_message_id'],creator=worker.job['creator'],topic_url=item['topic_url'],
            filename=name,bytes_total=item['bytes_total'],release_month=group['month'],batch_id='repair-'+worker.job['id'])
        if row['topic_url']!=item['topic_url'] or row['filename']!=name or row['bytes_total'] not in (None,item['bytes_total']):
            raise ValueError('Repair differs from saved attachment history.')
        repair['history_id']=row['id']
        directory=worker.store.root/'data/organizer-repairs'/worker.job['id']/str(item['source_message_id'])
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        try:
            if not repair.get('download'):
                meter=None
                def progress(done,total,estimate=None):
                    nonlocal meter
                    if meter is None:meter=TransferMeter()
                    metrics=meter.sample(done,finished=done==total);repair['metrics']=metrics
                    worker.store.history.record_transfer(row['id'],done,total,metrics,estimate)
                    worker.progress('repair_download','Downloading replacement · '+name,done,total,
                                    speed_bps=metrics['download_speed_bps'],elapsed_seconds=metrics['download_seconds'])
                def status(event):
                    nonlocal meter
                    kind=event.get('event')
                    if kind=='download_start':meter=None
                    message={'waiting_for_telegram':'Waiting for the current Telegram operation',
                             'server_testing':'Comparing download servers','server_selected':'Downloading replacement',
                             'speed_retest_pending':'Two-minute slowdown detected; server check queued for the next file',
                             'speed_retest_queued':'Two-minute slowdown detected; server check queued for the next file',
                             'download_resumed':'Resuming replacement download',
                             'server_slow_retest':'Comparing servers after sustained slowdown',
                             'server_test_fallback':'Keeping the previous server after unavailable comparison',
                             'server_retry':'Retrying download connection','download_finished':'Verifying downloaded part'}.get(kind)
                    if kind=='server_slow_retest' and event.get('reason')=='below_half_threshold':
                        message='Speed below half the threshold; comparing servers'
                    if message:worker.progress('repair_download',message+' · '+name,speed_bps=None)
                client=TelegramCLI(root=worker.store.root,config=worker.store.config())
                try:source=client.download(item,progress,worker.stopped,status)
                finally:client.close()
                if source.is_symlink() or source.stat().st_size!=item['bytes_total']:raise ValueError('Replacement download has an incomplete size.')
                repair['download']={'path':str(source),'size':source.stat().st_size,'sha256':digest(source)};worker.save()
            proof=repair['download'];source=Path(proof['path'])
            if source.is_symlink() or source.stat().st_size!=item['bytes_total'] or digest(source)!=proof['sha256']:
                raise ValueError('Staged replacement failed checksum verification.')
            backup_original(worker,pinned,repair,directory)
            destination=group['month']+'/'+name
            # A monthly original must be removed from its destination first;
            # its fsynced backup and verified replacement are already durable.
            if original and original['source']==destination and not repair.get('delivered'):
                current=pinned.info(destination)
                recovered_publish=(repair.get('removal_started') and current and current['size']==proof['size']
                                   and digest(pinned.path/destination)==proof['sha256'])
                if not recovered_publish:remove_original(worker,pinned,repair)
            meter=TransferMeter()
            def moving(done,total):
                worker.check();metrics=meter.sample(done)
                worker.progress('repair_move','Moving replacement to the release folder · '+name,done,total,speed_bps=metrics['download_speed_bps'])
            def phase(value):
                worker.check();worker.progress('repair_move',('Verifying replacement' if value=='verifying' else 'Moving replacement')+' · '+name,speed_bps=None)
            target,size,checksum=deliver(source,worker.job['base'],worker.job['creator_folder'],group['month'],name,moving,phase)
            if checksum!=proof['sha256'] or size!=item['bytes_total']:raise ValueError('Delivered replacement did not match its verified download.')
            repair['delivered']=True;worker.save()
            if original and original['source']!=destination:remove_original(worker,pinned,repair)
            record={'filename':name,'source':destination,'destination':destination,'month':group['month'],'size':size,
                    'identity':pinned.info(destination),'action':'keep','telegram_status':'matched','would_record':True,
                    'source_message_ids':[item['source_message_id']],'source_message_id':item['source_message_id'],
                    'message_url':item['message_url'],'telegram_name':name,'telegram_bytes':size,'telegram_size':f'{size:,} bytes',
                    'match_kind':'Verified repair download','repair_download':{'sha256':checksum}}
            group['records'].append(record);repair['installed']=True
            worker.job['files_total']+=1
            if original:worker.job['skipped_files']=max(0,worker.job['skipped_files']-1)
            worker.save()
            path=worker.store.root/'data/organizer-plan.json';plan=json.loads(path.read_text())
            if plan.get('id')==worker.job['plan_id']:
                if original:plan['files']=[f for f in plan['files'] if f['source']!=original['source']]
                plan['files'].append(record);worker.store.atomic_write(path,plan)
        except Exception as error:
            with worker.store.history.connect() as db:
                db.execute("UPDATE downloads SET state='paused',error=? WHERE id=? AND state!='downloaded'",(str(error),row['id']))
            raise


def cleanup(worker, group):
    for repair in group.get('repairs',[]):
        if not repair.get('installed') or repair.get('staging_removed'):continue
        try:
            source=Path(repair['download']['path'])
            if source.exists():
                if source.is_symlink() or digest(source)!=repair['download']['sha256']:continue
                source.unlink()
            client=TelegramCLI(root=worker.store.root,config=worker.store.config())
            try:client.cleanup_transfer(repair['item'])
            finally:client.close()
            repair['staging_removed']=True;worker.save()
        except (OSError,RuntimeError,ValueError):pass # Verified NAS file and history already committed; retry cleanup later.
