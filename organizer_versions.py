"""Choose whole archive uploads and preserve replaced files during organization."""
import copy
from collections import defaultdict
from pathlib import Path
import os
import shutil

from file_delivery import deliver, digest
from release_images import is_archive, listing, complete_split_7z, volume_key
from telegram_cli import TelegramCLI, safe_filename
from transfer_metrics import TransferMeter


def uploads(items):
    """A repeated volume starts another upload; parts need not be consecutive posts."""
    batches=[];current={}
    for item in sorted(items,key=lambda a:a['source_message_id']):
        index=volume_key(item['filename'])[1]
        if index in current:
            batches.append(list(current.values()));current={}
        current[index]=item
    if current:batches.append(list(current.values()))
    return batches


def complete(items):
    from organizer_apply import check_parts
    try:check_parts({'records':[{'filename':a['filename']} for a in items]})
    except ValueError:return False
    return True


def choices(plan):
    """Select larger complete uploads, breaking ties by the later message ID.

    Only reuse an entire local set from one directory. Equal-sized first parts
    alone cannot identify which of two differently packed uploads they belong to.
    """
    if plan.get('version_groups') is not None:return copy.deepcopy(plan['version_groups'])
    sources=defaultdict(list);local=defaultdict(list)
    for item in plan.get('attachments',[]):
        if safe_filename(item.get('filename')) and is_archive(item['filename']):
            sources[volume_key(item['filename'])[0]].append(item)
    for row in plan.get('files',[]):
        if row.get('month') and is_archive(row['filename']):
            local[row['month']+'/'+volume_key(row['filename'])[0]].append(row)
    result={}
    for key,rows in local.items():
        variants=uploads(sources[key.split('/',1)[1]])
        signatures={tuple(sorted((a['filename'],a['bytes_total']) for a in v)) for v in variants}
        if len(signatures)<2:continue
        candidates=[v for v in variants if complete(v)]
        if not candidates:continue
        preferred=max(candidates,key=lambda v:(sum(a['bytes_total'] for a in v),max(a['source_message_id'] for a in v)))
        preferred=sorted(preferred,key=lambda a:volume_key(a['filename'])[1])
        token=str(max(a['source_message_id'] for a in preferred));month=key.split('/',1)[0]
        folders=defaultdict(list)
        for row in rows:folders[str(Path(row['source']).parent)].append(row)
        sets=[]
        for folder,files in folders.items():
            matched=[[r for r in files if r['filename']==a['filename'] and r['size']==a['bytes_total'] and r.get('identity')] for a in preferred]
            if all(len(matches)==1 for matches in matched):sets.append([matches[0] for matches in matched])
        # Multiple indistinguishable copies need the chosen Telegram upload;
        # never guess which same-sized local bytes were uploaded later.
        selected=sets[0] if len(sets)==1 else []
        release_dir=rows[0].get('release_directory') or month
        records=[]
        for index,item in enumerate(preferred):
            record=copy.deepcopy(selected[index]) if selected else {'filename':item['filename'],'source':item['filename'],'size':item['bytes_total'],'identity':None}
            record.update(month=month,release_directory=release_dir,destination=release_dir+'/'+item['filename'],
                action='keep' if selected and record['source']==release_dir+'/'+item['filename'] else 'move',
                telegram_status='matched',source_message_ids=[item['source_message_id']],
                source_message_id=item['source_message_id'],message_url=item['message_url'],
                telegram_name=item['filename'],telegram_bytes=item['bytes_total'],telegram_size=f"{item['bytes_total']:,} bytes",
                already_recorded=False,would_record=True,archive_version=token,version_download=not bool(selected),
                reason='Preferred archive version: largest complete upload; later upload breaks a size tie.'+(' Download required.' if not selected else ''))
            records.append(record)
        used={r['source'] for r in selected}
        previous=[copy.deepcopy(r) for r in rows if r['source'] not in used]
        result[key]={'key':key,'month':month,'release_directory':release_dir,'records':records,'state':'pending',
            'version':{'id':token,'items':copy.deepcopy(preferred),'bytes_total':sum(a['bytes_total'] for a in preferred),
                       'variants':len(variants),'download':not bool(selected)},'previous_files':previous}
    return result


def project(plan, history):
    """Show the chosen upload without rewriting the saved preview or receipts."""
    groups=choices(plan)
    if not groups:return
    plan['version_groups']=groups
    plan['files']=[r for r in plan.get('files',[]) if (r.get('month') or '')+'/'+volume_key(r['filename'])[0] not in groups]
    for group in groups.values():plan['files'].extend(copy.deepcopy(group['records']))
    with history.connect() as db:
        done={r['source_message_id'] for r in db.execute("SELECT source_message_id FROM downloads WHERE topic_url=? AND state='downloaded'",(plan['topic_url'],))}
    for row in plan['files']:
        if row.get('archive_version'):
            row['already_recorded']=all(mid in done for mid in row['source_message_ids'])
            row['would_record']=not row['already_recorded']
    plan['files'].sort(key=lambda r:r['source'])
    plan['archive_versions']=[{'month':g['month'],'name':g['key'].split('/',1)[1],
        'bytes_total':g['version']['bytes_total'],'files':len(g['records']),
        'download_files':len(g['records']) if g['version']['download'] else 0,
        'backup_files':len(g['previous_files'])} for g in groups.values()]


def upgrade(job, plan):
    """Upgrade only unfinished legacy groups; retain completed work and staging."""
    selected=choices(plan);changed=False
    for index,old in enumerate(job['groups']):
        if old['state']=='completed' or old.get('version') or old['key'] not in selected:continue
        if any(r.get('move_started') or r.get('recorded') for r in old['records']):
            raise ValueError('An older archive version was partly moved. Run a fresh preview before selecting its replacement.')
        group=selected[old['key']]
        group['work_index']='version-'+group['version']['id']
        downloads={str(r['item']['source_message_id']):copy.deepcopy(r) for r in old.get('repairs',[]) if r.get('download')}
        group['version_downloads']={str(a['source_message_id']):downloads[str(a['source_message_id'])]
            for a in group['version']['items'] if str(a['source_message_id']) in downloads}
        job['groups'][index]=group;changed=True
    if changed:
        job.update(files_total=sum(len(g['records']) for g in job['groups']),
            files_done=sum(g['state']=='completed' or bool(r.get('recorded')) for g in job['groups'] for r in g['records']),
            moves_total=sum(r['action']=='move' for g in job['groups'] for r in g['records']),
            moves_done=sum(r['action']=='move' and bool(r.get('moved')) for g in job['groups'] for r in g['records']),
            current_release=None,phase='preparing',progress_done=0,progress_total=None)
    return changed


def download_inputs(worker, group, work):
    inputs=work/'preferred-inputs';inputs.mkdir(exist_ok=True)
    proofs=group.setdefault('version_downloads',{})
    locations=[]
    for item,record in zip(group['version']['items'],group['records']):
        worker.check()
        if item.get('topic_url')!=worker.job['topic_url'] or worker.store.history.source.topic_id(item.get('topic_url')) is None:
            raise ValueError('Preferred upload is outside the selected creator.')
        key=str(item['source_message_id']);proof=proofs.setdefault(key,{'item':item})
        row=worker.store.history.register(source_message_id=item['source_message_id'],creator=worker.job['creator'],topic_url=item['topic_url'],
            filename=item['filename'],bytes_total=item['bytes_total'],release_month=group['month'],batch_id='repair-'+worker.job['id'])
        if (row['topic_url'],row['filename'],row['bytes_total'])!=(item['topic_url'],item['filename'],item['bytes_total']):
            raise ValueError('Preferred upload differs from saved download history.')
        if not proof.get('download'):
            meter=TransferMeter()
            def progress(done,total,estimate=None):
                metrics=meter.sample(done,finished=done==total)
                worker.store.history.record_transfer(row['id'],done,total,metrics,estimate)
                worker.progress('repair_download','Downloading preferred version · '+item['filename'],done,total,
                    speed_bps=metrics['download_speed_bps'],elapsed_seconds=metrics['download_seconds'])
            def status(event):
                if event.get('event')=='waiting_for_telegram':worker.progress('repair_download','Waiting for Telegram · '+item['filename'])
            client=TelegramCLI(root=worker.store.root,config=worker.store.config())
            try:source=client.download(item,progress,worker.stopped,status)
            finally:client.close()
            if source.is_symlink() or source.stat().st_size!=item['bytes_total']:raise ValueError('Preferred archive download has an incomplete size.')
            proof['download']={'path':str(source),'size':source.stat().st_size,'sha256':digest(source)};worker.save()
        saved=proof['download'];source=Path(saved['path'])
        if source.is_symlink() or source.stat().st_size!=item['bytes_total'] or digest(source)!=saved['sha256']:
            raise ValueError('Saved preferred archive changed; originals retained.')
        target=inputs/item['filename']
        if not target.exists():
            try:os.link(source,target)
            except OSError:shutil.copyfile(source,target)
        if target.is_symlink() or target.stat().st_size!=saved['size'] or digest(target)!=saved['sha256']:
            raise ValueError('Staged preferred archive changed; originals retained.')
        record['repair_download']={'sha256':saved['sha256']}
        locations.append((record,target))
    return locations


def apply(worker, group, pinned, work):
    """Validate/extract the complete winner before backing up or moving originals."""
    from organizer_apply import check_parts
    items=group['version']['items']
    if len(items)!=len(group['records']):raise ValueError('Preferred archive selection changed.')
    for item,record in zip(items,group['records']):
        if (not safe_filename(item['filename']) or item.get('topic_url')!=worker.job['topic_url']
                or worker.store.history.source.topic_id(item.get('topic_url')) is None
                or item.get('message_url')!=item['topic_url']+'/'+str(item['source_message_id'])
                or record['filename']!=item['filename'] or record['size']!=item['bytes_total']
                or record['source_message_ids']!=[item['source_message_id']]
                or record['destination']!=group.get('release_directory',group['month'])+'/'+item['filename']):
            raise ValueError('Preferred archive differs from its exact Telegram metadata.')
    check_parts(group)
    if group['version']['download']:
        locations=download_inputs(worker,group,work)
    else:
        locations=[]
        for record in group['records']:
            source=record['source']
            if record.get('move_started') and pinned.info(source) is None:source=record['destination']
            if pinned.info(source)!=record['identity']:raise ValueError('Preferred archive changed since preview: '+source)
            locations.append((record,pinned.path/source))
    if not group.get('version_validated'):
        first=locations[0][1]
        worker.progress('preparing','Checking the complete preferred archive · '+first.name)
        header,_=listing(first,work,worker.stopped,lambda:worker.check())
        complete_split_7z(first,header)
        group['version_validated']=True;worker.save()
    images=worker.images(group,pinned,work,locations=locations)
    # Check every previous file before beginning the recoverable rename sequence.
    for previous in group['previous_files']:
        if previous.get('backed_up'):
            if pinned.info(previous['backup_record']['destination'])!=previous['identity']:
                raise ValueError('Saved previous archive changed; replacement needs review.')
        elif previous.get('backup_record'):pinned.locate(previous['backup_record'])
        elif pinned.info(previous['source'])!=previous['identity']:
            raise ValueError('Previous archive changed since preview: '+previous['source'])
    for index,previous in enumerate(group['previous_files']):
        if previous.get('backed_up'):continue
        worker.check()
        backup=previous.get('backup_record')
        if not backup:
            backup=copy.deepcopy(previous)
            backup.update(destination=group.get('release_directory',group['month'])+'/.previous_versions/'+worker.job['id']+'/'+str(index)+'/'+previous['filename'],move_started=True)
            previous['backup_record']=backup;worker.save()
        worker.progress('version_backup','Keeping the previous archive version · '+previous['filename'])
        pinned.move(backup);previous['backed_up']=True;worker.save()
    for record,source in locations:
        worker.check()
        if record.get('version_download'):
            meter=TransferMeter()
            def progress(done,total):
                metrics=meter.sample(done)
                worker.progress('repair_move','Moving preferred archive · '+record['filename'],done,total,speed_bps=metrics['download_speed_bps'])
            target,size,checksum=deliver(source,worker.job['base'],worker.job['creator_folder'],group['month'],record['filename'],progress,release_directory=group.get('release_directory'))
            if checksum!=record['repair_download']['sha256'] or size!=record['size']:raise ValueError('Preferred archive delivery did not match its download.')
            record.update(identity=pinned.info(record['destination']),move_started=True)
        else:
            record['move_started']=True;worker.save();pinned.move(record)
        if not record.get('moved'):
            record['moved']=True
            if record['action']=='move':worker.job['moves_done']+=1
        worker.save()
    return images


def cleanup(worker, group):
    for proof in group.get('version_downloads',{}).values():
        if proof.get('staging_removed') or not proof.get('download'):continue
        try:
            source=Path(proof['download']['path'])
            if source.exists():
                if source.is_symlink() or digest(source)!=proof['download']['sha256']:continue
                source.unlink()
            client=TelegramCLI(root=worker.store.root,config=worker.store.config())
            try:client.cleanup_transfer(proof['item'])
            finally:client.close()
            proof['staging_removed']=True;worker.save()
        except (OSError,RuntimeError,ValueError):pass


def failed(worker, group, error):
    with worker.store.history.connect() as db:
        for item in group['version']['items']:
            db.execute("UPDATE downloads SET state='paused',error=? WHERE topic_url=? AND source_message_id=? AND state!='downloaded'",
                (str(error),worker.job['topic_url'],item['source_message_id']))
