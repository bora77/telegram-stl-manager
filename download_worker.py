#!/usr/bin/env python3
"""Finite manual subscription batch using the standalone Telegram CLI."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import shutil
import tempfile
import time
from datetime import datetime,timezone
from subscription_store import SubscriptionStore
from telegram_cli import TelegramCLI, CLIError as WorkerError, STATE, safe_filename
from release_rules import release_month,in_scope
from download_plan import DownloadPlan
from transfer_metrics import TransferMeter
from file_delivery import deliver,mount_identity,digest,release_lock
from release_images import extract_images,ExtractionError,MissingVolumeError,volume_key,flat_image_name,listing

ROOT=Path(__file__).resolve().parent
class Worker:
    def __init__(self,batch):
        self.store=SubscriptionStore(ROOT);self.history=self.store.history
        self.path=ROOT/'data/run.json';self.run=json.loads(self.path.read_text())
        if self.run['id']!=batch:raise RuntimeError('Run was replaced before start.')
        self.batch=batch;self.telegram=None;self.pending=[];self.planned=[];self.numbered_rars=set()
        self.plan=DownloadPlan(self.store,self.run)
    def archive_key(self,name,sub,month):
        match=re.fullmatch(r'(.+)[ _.-](\d+)\.rar',name,re.I)
        if match and (sub['topic_url'],month,match[1].casefold()) in self.numbered_rars:
            return 'numbered-rar:'+match[1].casefold(),int(match[2])
        return volume_key(name)
    def discover_numbered_rar(self,record,error):
        # A bare number can also label an independent release. Only group it
        # when 7-Zip explicitly identifies a companion of this outer archive.
        first=re.fullmatch(r'(.+)[ _.-](\d+)\.rar',record['filename'],re.I)
        missing=re.fullmatch(r'(.+)[ _.-](\d+)\.rar',error.missing,re.I)
        if error.archive.name!=record['filename'] or error.archive.parent.name!='inputs' or not first or not missing or first[1].casefold()!=missing[1].casefold():return False
        self.numbered_rars.add((record['sub']['topic_url'],record['month'],first[1].casefold()))
        return True
    def stage_part(self,record):
        self.pending.append(record)
        with self.history.connect() as db:db.execute("UPDATE downloads SET state='queued',error=NULL,bytes_downloaded=? WHERE id=?",(record['staged'].stat().st_size,record['id']))
        self.update('downloading','Archive part ready locally; collecting the remaining parts · '+record['filename'])
    def stopped(self):return (ROOT/'data/stop-request').exists()
    def update(self,state,message,**extra):
        self.run.update(state=state,message=message,**extra);self.store.atomic_write(self.path,self.run)
    def warn(self,message):
        if message not in self.run['warnings']:self.run['warnings'].append(message)
        self.store.atomic_write(self.path,self.run)
    def pause(self,records,error):
        with self.history.connect() as db:
            for record in records:
                db.execute("UPDATE downloads SET state='paused',error=? WHERE id=? AND state!='downloaded'",(str(error),record['id']))
    def transfer(self,sub,item,month):
        filename=item['filename']
        if self.history.was_downloaded(item['source_message_id']):return
        row=self.history.register(source_message_id=item['source_message_id'],creator=sub['creator'],topic_url=sub['topic_url'],filename=filename,bytes_total=item.get('bytes_total'),release_month=month,batch_id=self.batch)
        item_id=row['id']
        with self.history.connect() as db:db.execute("UPDATE downloads SET batch_id=?,state='queued',error=NULL WHERE id=? AND state!='downloaded'",(self.batch,item_id))
        stagedir=STATE/'staging'/str(item_id);stagedir.mkdir(parents=True,exist_ok=True,mode=0o700)
        staged=stagedir/filename;receipt=stagedir/'receipt.json'
        record={'id':item_id,'staged':staged,'receipt':receipt,'filename':filename,'month':month,'sub':sub}
        try:
            mount_identity(self.run['download_directory'])
            if not (staged.is_file() and receipt.is_file()):
                if staged.exists():raise WorkerError('An unverified staging file needs review: '+filename)
                self.update('downloading','Downloading '+filename,current_creator=sub['creator'],download_server=None,server_stage=None,server_test_reason=None)
                meter=None
                def progress(done,total,estimate=None):
                    nonlocal meter
                    if meter is None:meter=TransferMeter()
                    self.history.record_transfer(item_id,done,total,meter.sample(done,finished=total is not None and done==total),estimate)
                def cli_status(event):
                    nonlocal meter
                    kind=event.get('event')
                    if kind=='waiting_for_telegram':
                        self.update('downloading','Waiting for the current Telegram scan to finish · '+filename)
                    elif kind=='download_start':meter=None
                    if kind=='speed_retest_pending':
                        self.update('downloading',f"Speed below {event['threshold_bps']/1e6:g} MB/s for at least five minutes; server check queued for the next file. Downloading "+filename)
                    elif kind=='server_slow_retest':
                        self.update('downloading','Comparing servers after sustained slow download speed…',server_test_reason='sustained_slowdown')
                    elif kind=='server_test_fallback':
                        self.update('downloading','Server comparison unavailable; keeping the previous server.')
                    if kind=='server_testing':
                        label='Comparing servers after sustained slowdown' if self.run.get('server_test_reason')=='sustained_slowdown' else 'Comparing download servers'
                        self.update('downloading',f"{label} · {event['index']} / {event['count']}",server_test=event,server_stage='testing')
                    elif kind=='server_selected':
                        self.update('downloading','Downloading '+filename,download_server=event['endpoint'],server_stage='selected')
                    elif kind=='server_retry':
                        self.update('downloading','Connection failed; comparing servers again before retrying.',server_stage='retrying')
                    elif kind=='download_finished':
                        self.update('downloading','Verifying downloaded file · '+filename)
                source=self.telegram.download(item,progress,self.stopped,cli_status)
                os.rename(source,staged)
                self.store.atomic_write(receipt,{'message_url':item['message_url'],'size':staged.stat().st_size,'sha256':digest(staged)})
                if hasattr(self.telegram,'cleanup_transfer'):self.telegram.cleanup_transfer(item)
            proof=json.loads(receipt.read_text())
            if proof['message_url']!=item['message_url'] or staged.stat().st_size!=proof['size']:raise WorkerError('Staged file does not match its download receipt.')
            if item.get('bytes_total') is not None and staged.stat().st_size!=item['bytes_total']:raise WorkerError('Staged file differs from Telegram’s exact file size.')
            if proof.get('sha256') and digest(staged)!=proof['sha256']:raise WorkerError('Staged file failed checksum verification.')
            if self.archive_key(filename,sub,month)[1]>0:
                self.stage_part(record)
                return
            try:self.finish_release([record])
            except MissingVolumeError as error:
                if self.stopped():raise
                if self.discover_numbered_rar(record,error):self.stage_part(record)
                elif Path(filename).suffix.lower() in ('.rar','.zip'):self.pending.append(record)
                else:raise
            except ExtractionError:
                if self.stopped():raise
                # A .rar or .zip can be the first volume even without a numbered
                # suffix. Retry with any remaining parts after the creator scan.
                if Path(filename).suffix.lower() not in ('.rar','.zip'):raise
                self.pending.append(record)
        except Exception as error:
            self.pause([record],error)
            raise
    def move_one(self,source,sub,month,filename,subdirectories=()):
        move_meter=TransferMeter();copied_bytes=0;move_clock=None;move_result=None
        self.update('copying','Moving '+filename+' to destination',copy_filename=filename,copy_bytes=0,copy_total=source.stat().st_size,move_stage='preparing',move_seconds=0,move_speed_bps=None,move_average_bps=None)
        def copy_progress(done,total):
            nonlocal copied_bytes
            copied_bytes=done
            if self.stopped():raise WorkerError('Stopped by request; staged release kept.')
            measured=move_meter.sample(done)
            self.update('copying','Moving '+filename+' to destination',copy_bytes=done,copy_total=total,move_seconds=measured['download_seconds'],move_speed_bps=measured['download_speed_bps'],move_average_bps=measured['download_average_bps'])
        def copy_phase(phase):
            nonlocal move_meter,move_clock,move_result
            if self.stopped():raise WorkerError('Stopped by request; staged release kept.')
            if phase=='transferring':move_meter=TransferMeter();move_clock=time.monotonic()
            extra={}
            if phase=='verifying' and move_clock is not None:
                elapsed=time.monotonic()-move_clock
                move_result={'seconds':elapsed,'average':copied_bytes/elapsed if elapsed>0 else None}
                extra={'move_seconds':elapsed,'move_average_bps':move_result['average']}
            message='Verifying '+filename+' on Kronos…' if phase=='verifying' else 'Moving '+filename+' to Kronos…' if phase=='transferring' else 'Preparing move to Kronos…'
            self.update('copying',message,move_stage=phase,move_speed_bps=None,**extra)
        destination,size,checksum=deliver(source,self.run['download_directory'],sub['creator_folder'],month,filename,copy_progress,copy_phase,subdirectories=subdirectories)
        move_result=move_result or {'seconds':None,'average':None}
        return {'destination':str(destination),'size':size,'sha256':checksum,'move_seconds':move_result['seconds'],'move_average_bps':move_result['average']}
    def finish_release(self,records):
        sub=records[0]['sub'];month=records[0]['month'];key=lambda name:self.archive_key(name,sub,month)
        records=sorted(records,key=lambda r:key(r['filename'])[1]);first=records[0]['filename']
        try:
            # A companion may have completed before image extraction was added.
            # Reuse its verified NAS copy without downloading or moving it again.
            companions=[];names={r['filename'] for r in records}
            if any(key(name)[1]>0 for name in names):
                group_key=key(first)[0]
                with self.history.connect() as db:
                    completed=[dict(r) for r in db.execute("SELECT * FROM downloads WHERE state='downloaded' AND topic_url=? AND release_month=?",(sub['topic_url'],month))]
                for row in completed:
                    if row['filename'] not in names and key(row['filename'])[0]==group_key:
                        companions.append(row);names.add(row['filename'])
                first=min(names,key=lambda name:key(name)[1])
            if key(first)[1]>1:raise ExtractionError('First archive volume is missing; all downloaded parts were kept locally.')
            image_destination=Path(self.run['download_directory'])/sub['creator_folder']/month/'release_images'
            archives=[{'filename':r['filename'],'size':r['staged'].stat().st_size} for r in records]
            archives.extend({'filename':r['filename'],'size':r['bytes_total']} for r in companions)
            saved=self.history.image_extraction(sub['topic_url'],archives,image_destination)
            if saved:
                manifest=saved['images']
                self.update('extracting','Images already extracted; using saved status.',images_done=len(manifest),images_total=len(manifest))
            else:
                with tempfile.TemporaryDirectory(prefix='release-work-',dir=STATE/'staging') as temporary:
                    work=Path(temporary);inputs=work/'inputs';inputs.mkdir()
                    for record in records:
                        os.link(record['staged'],inputs/record['filename'])
                    for companion in companions:self.stage_completed_part(companion,inputs,sub,month)
                    if key(first)[0].startswith('numbered-rar:'):
                        header,_=listing(inputs/first,work,self.stopped)
                        volumes=re.search(r'^Volumes = (\d+)$',header,re.M)
                        indexes=sorted(key(name)[1] for name in names)
                        if 'Multivolume = +' not in header or not volumes or int(volumes[1])!=len(names) or indexes!=list(range(1,len(names)+1)):
                            raise ExtractionError('Numbered RAR files do not form one complete archive set; all parts were kept locally.')
                    started=time.monotonic();last_update=0;last_stage=None
                    def extraction_progress(stage,count,total,done,expected):
                        nonlocal last_update,last_stage
                        if self.stopped():raise ExtractionError('Stopped by request; staged release kept.')
                        now=time.monotonic()
                        if stage==last_stage and stage!='complete' and now-last_update<.8:return
                        last_update=now;last_stage=stage
                        label={'listing':'Reading archive contents','checking_parts':'Checking split archive integrity','extracting':'Extracting release images','complete':'Image extraction complete'}[stage]
                        self.update('extracting',label+' · '+first,current_creator=sub['creator'],extraction_stage=stage,images_done=count,images_total=total,extraction_bytes=done,extraction_total=expected,extraction_seconds=now-started)
                    name_changes=[]
                    images=extract_images(inputs/first,work/'images',extraction_progress,self.stopped,name_changes=name_changes)
                    for message in name_changes:self.warn(message)
                    manifest=[]
                    for image in images:
                        relative=image.relative_to(work/'images'/'output')
                        name=flat_image_name(first,relative)
                        result=self.move_one(image,sub,month,name,('release_images',))
                        manifest.append({'path':name,'source_path':str(relative),'size':result['size'],'sha256':result['sha256']})
            self.history.record_image_extraction(sub['topic_url'],archives,manifest,image_destination,
                                                 warnings=saved['warnings'] if saved else name_changes)
            delivered=[]
            for record in records:
                result=self.move_one(record['staged'],sub,month,record['filename'])
                delivered.append(dict(result,id=record['id']))
            self.history.complete_release(delivered,manifest,image_destination)
            # No volume is removed before the entire release commits together.
            for record in records:
                record['staged'].unlink();record['receipt'].unlink();record['staged'].parent.rmdir()
        except Exception as error:
            self.pause(records,error)
            raise
    def stage_completed_part(self,row,inputs,sub,month):
        base=Path(self.run['download_directory']);mount_identity(base)
        filename=row['filename']
        if Path(filename).name!=filename or filename in ('.','..'):raise ExtractionError('Invalid saved archive part name.')
        source=base/sub['creator_folder']/month/filename
        if str(source)!=row['original_destination'] or any(path.is_symlink() for path in (source,*source.parents)):
            raise ExtractionError('Previously downloaded archive part needs review at its recorded destination: '+filename)
        imported_without_hash=row.get('origin')=='organizer' and row['sha256'] is None
        if not source.is_file() or source.stat().st_size!=row['bytes_total'] or (not imported_without_hash and not re.fullmatch('[0-9a-f]{64}',row['sha256'] or '')):
            raise ExtractionError('Previously downloaded archive part is missing or changed: '+filename)
        original=source.stat()
        if shutil.disk_usage(inputs).free<row['bytes_total']+1024**3:
            raise ExtractionError('Not enough staging space to reuse a downloaded archive part.')
        checksum=hashlib.sha256();done=0;started=time.monotonic();last_update=0
        with source.open('rb') as incoming,(inputs/filename).open('xb') as out:
            while block:=incoming.read(4*1024*1024):
                if self.stopped():raise ExtractionError('Stopped while preparing archive parts; originals retained.')
                if done+len(block)>row['bytes_total']:raise ExtractionError('Previously downloaded archive part changed size.')
                out.write(block);checksum.update(block);done+=len(block)
                now=time.monotonic()
                if now-last_update>.8:
                    self.update('extracting','Preparing previously downloaded archive part · '+filename,
                                extraction_stage='preparing_parts',extraction_bytes=done,extraction_total=row['bytes_total'],
                                images_done=0,images_total=None,extraction_seconds=now-started)
                    last_update=now
        current=source.stat()
        unchanged=(current.st_size,current.st_mtime_ns,current.st_ino)==(original.st_size,original.st_mtime_ns,original.st_ino)
        valid_hash=digest(inputs/filename)==checksum.hexdigest() if imported_without_hash else checksum.hexdigest()==row['sha256']
        if done!=row['bytes_total'] or not unchanged or not valid_hash:
            raise ExtractionError('Previously downloaded archive part failed checksum verification: '+filename)
    def finish_pending(self,sub):
        groups={}
        for record in self.pending:
            if record['sub']['topic_url']==sub['topic_url']:
                groups.setdefault((record['month'],self.archive_key(record['filename'],sub,record['month'])[0]),[]).append(record)
        for records in groups.values():self.finish_release(records)
        self.pending=[r for r in self.pending if r['sub']['topic_url']!=sub['topic_url']]
    def scan(self,sub):
        topic=sub['topic_url'];received=0;started=time.monotonic()
        def status(message):
            self.update('scanning',sub['creator']+' · '+message,current_creator=sub['creator'],files_listed=received,scan_page=None,creators_checked=len(self.run.get('creator_results',[])))
        def on_files(files):
            nonlocal received
            received+=len(files);status('Reading new attachment metadata…')
        status('Checking for new releases…')
        items=self.telegram.list_files(topic,self.stopped,status,on_files=on_files,incremental=True)
        result={'creator':sub['creator'],'latest_release_month':None,'eligible_files':0,'already_downloaded':0,'outside_scope':0}
        result.update(scan_seconds=time.monotonic()-started,**{k:v for k,v in getattr(self.telegram,'last_scan',{}).items() if k in ('mode','new_files','catalog_files')})
        self.run.setdefault('creator_results',[]).append(result)
        with self.history.connect() as db:
            downloaded={r['source_message_id'] for r in db.execute("SELECT source_message_id FROM downloads WHERE topic_url=? AND state='downloaded'",(topic,))}
        warnings=[]
        for item in items:
            if self.stopped():raise WorkerError('Stopped by request.')
            if not safe_filename(item['filename']):
                warnings.append(sub['creator']+': unsafe attachment filename needs review.');continue
            month=release_month(item['filename'])
            if month is None:
                warnings.append(sub['creator']+': release month needs review for '+item['filename']);continue
            result['latest_release_month']=max(result['latest_release_month'] or month,month)
            if not in_scope(sub,month):result['outside_scope']+=1;continue
            result['eligible_files']+=1
            if item['source_message_id'] in downloaded:
                result['already_downloaded']+=1;continue
            self.queue_item(sub,item,month)
        self.run['warnings']=list(dict.fromkeys(self.run['warnings']+warnings))
        self.plan.save(sub,[{'item':item,'month':month} for saved_sub,item,month in self.planned if saved_sub['topic_url']==topic],result,warnings)
        self.update('scanning',f"{sub['creator']} · check complete",files_listed=received,telegram_file_count=len(items),creators_checked=len(self.run['creator_results']))
    def queue_item(self,sub,item,month):
        if self.history.was_downloaded(item['source_message_id']):return
        item={**item,'topic_url':sub['topic_url']}
        row=self.history.register(source_message_id=item['source_message_id'],creator=sub['creator'],topic_url=sub['topic_url'],
                                  filename=item['filename'],bytes_total=item['bytes_total'],release_month=month,batch_id=self.batch)
        if row['filename']!=item['filename'] or row['topic_url']!=sub['topic_url']:
            raise WorkerError('Previously queued attachment identity changed; review required.')
        stagedir=STATE/'staging'/str(row['id']);staged=stagedir/item['filename'];receipt=stagedir/'receipt.json'
        reuse=row.get('download_finished_at') and staged.is_file() and not staged.is_symlink() and receipt.is_file() and not receipt.is_symlink()
        with self.history.connect() as db:
            reset='' if reuse else ',bytes_downloaded=0,download_started_at=NULL,download_finished_at=NULL,download_seconds=NULL,download_speed_bps=NULL,download_average_bps=NULL'
            db.execute("UPDATE downloads SET batch_id=?,state='queued',error=NULL,bytes_total=?"+reset+" WHERE id=? AND state!='downloaded'",(self.batch,item['bytes_total'],row['id']))
        self.planned.append((sub,item,month))
    def cleanup_completed(self):
        # Only the worker owns these scratch directories. Download receipts and
        # source volumes live separately and survive an interrupted extraction.
        for scratch in (STATE/'staging').glob('release-work-*'):
            if scratch.is_dir() and not scratch.is_symlink():shutil.rmtree(scratch)
        with self.history.connect() as db:
            rows=[dict(r) for r in db.execute("SELECT * FROM downloads WHERE state='downloaded'")]
        for row in rows:
            directory=STATE/'staging'/str(row['id']);staged=directory/row['filename'];receipt=directory/'receipt.json'
            if staged.is_file() and not staged.is_symlink() and receipt.is_file() and digest(staged)==row['sha256']:
                staged.unlink();receipt.unlink();directory.rmdir()
    def execute(self):
        try:
            self.cleanup_completed()
            self.telegram=TelegramCLI(root=ROOT,config=self.run.get('config',self.store.config()))
            if self.run.get('resume_requested'):self.plan.seed_legacy()
            self.run.update(plan_version=1,creator_results=[],creators_done=0,creators_checked=0)
            for sub in self.run['subscriptions']:
                if self.stopped():raise WorkerError('Stopped by request.')
                saved=self.plan.load(sub)
                if saved is None:self.scan(sub)
                else:
                    for entry in saved['items']:
                        if self.stopped():raise WorkerError('Stopped by request.')
                        self.queue_item(sub,entry['item'],entry['month'])
                    self.run['creator_results'].append(saved['result'])
                    self.run['warnings']=list(dict.fromkeys(self.run['warnings']+saved['warnings']))
                    self.update('scanning','Using saved queue · '+sub['creator'],creators_checked=len(self.run['creator_results']),files_listed=0)
            for sub in self.run['subscriptions']:
                planned=sorted((p for p in self.planned if p[0]['topic_url']==sub['topic_url']),key=lambda p:(p[2],self.archive_key(p[1]['filename'],sub,p[2])))
                from itertools import groupby
                for month,monthly in groupby(planned,key=lambda p:p[2]):
                    def waiting():self.update('downloading','Waiting for organization of '+sub['creator']+' · '+month,current_creator=sub['creator'])
                    with release_lock(ROOT,self.run['download_directory'],sub['creator_folder'],month,self.stopped,waiting):
                        group=None
                        for _,item,_ in monthly:
                            next_group=self.archive_key(item['filename'],sub,month)[0]
                            if group is not None and group!=next_group:self.finish_pending(sub)
                            # transfer checks history again after acquiring the
                            # month: the organizer may have completed it meanwhile.
                            self.transfer(sub,item,month)
                            group=self.archive_key(item['filename'],sub,month)[0]
                        self.finish_pending(sub)
                self.run['creators_done']+=1
            self.update('needs_review' if self.run['warnings'] else 'completed', 'Manual run finished. '+('Some items need review; see details below.' if self.run['warnings'] else 'All eligible discovered files are handled.'),finished_at=datetime.now(timezone.utc).isoformat())
        except Exception as error:
            self.update('stopped' if self.stopped() else 'failed',str(error),finished_at=datetime.now(timezone.utc).isoformat())
        finally:
            if self.telegram:self.telegram.close()

if __name__=='__main__':
    os.umask(0o077)
    with (ROOT/'data/worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        Worker(sys.argv[1]).execute()
