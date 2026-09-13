"""Manually applied folder plans, with durable per-release recovery."""
import ctypes
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from subprocess import Popen
import sys
import time
import uuid
from datetime import datetime, timezone

from file_delivery import deliver, digest, mount_identity, release_lock
from release_images import extract_images, flat_image_name, volume_key
from subscription_store import SubscriptionStore
from telegram_cli import safe_filename
from transfer_metrics import TransferMeter

ROOT = Path(__file__).resolve().parent
RUNNING = ('starting', 'running')
RETRYABLE = ('failed', 'stopped', 'interrupted')


def identity(info):
    return {'size': info.st_size, 'mtime_ns': info.st_mtime_ns,
            'device': info.st_dev, 'inode': info.st_ino}


def application_status(store):
    path = store.root / 'data/organizer-apply.json'
    if not path.exists():return None
    job = json.loads(path.read_text())
    if job['state'] in RUNNING and time.time() - job.get('heartbeat', 0) > 30:
        with (store.root / 'data/organizer-worker.lock').open('a') as lock:
            try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:pass
            else:job.update(state='interrupted', message='Organization interrupted. Resume to finish this saved plan.')
    # Publish only the per-file outcome needed by the live table. The recovery
    # identities and extraction manifests remain in the private journal.
    result = {k: v for k, v in job.items() if k not in ('groups', 'mount')}
    result['files'] = []
    for group in job.get('groups', []):
        current = group['key'] == job.get('current_release')
        for record in group['records']:
            recorded = group['state'] == 'completed' or bool(record.get('recorded'))
            state = 'completed' if recorded else 'moved' if record.get('moved') else 'pending'
            if current and not recorded:
                if job['state'] in RUNNING:
                    state = job.get('phase', 'preparing')
                elif job['state'] in RETRYABLE:
                    state = 'paused'
            result['files'].append({'source': record['source'], 'destination': record['destination'],
                                    'moved': bool(record.get('moved')), 'recorded': recorded, 'state': state})
    return result


class PinnedFolder:
    """No-overwrite moves through handles pinned to the selected NAS folder."""
    def __init__(self, base, folder):
        self.base = Path(base)
        self.mount = mount_identity(self.base)
        if not safe_filename(folder):raise ValueError('Invalid artist folder.')
        basefd = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:self.fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=basefd)
        finally:os.close(basefd)
        self.path = self.base / folder
        if os.fstat(self.fd).st_dev != self.mount[-1]:
            self.close();raise ValueError('Artist folder is on a different filesystem.')

    def close(self):os.close(self.fd)

    def parent(self, relative, create=False):
        parts = PurePosixPath(relative).parts
        if not parts or PurePosixPath(relative).is_absolute() or any(not safe_filename(p) for p in parts):
            raise ValueError('Unsafe path in folder plan.')
        fd = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                if create:
                    try:os.mkdir(part, dir_fd=fd)
                    except FileExistsError:pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd);fd = child
                if os.fstat(fd).st_dev != self.mount[-1]:raise ValueError('Folder filesystem changed.')
            return fd, parts[-1]
        except BaseException:
            os.close(fd);raise

    def info(self, relative):
        try:fd, name = self.parent(relative)
        except FileNotFoundError:return None
        try:
            try:info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:return None
            if not stat.S_ISREG(info.st_mode):raise ValueError('A planned file was replaced by a link or directory.')
            return identity(info)
        finally:os.close(fd)

    def locate(self, record):
        source = self.info(record['source'])
        if source is not None:
            if source != record['identity']:raise ValueError('File changed since preview: ' + record['source'])
            if record['source'] != record['destination']:
                target=self.info(record['destination'])
                if target is not None and target != record.get('destination_identity'):
                    # A concurrent downloader may have delivered this exact
                    # archive since Preview. Reuse it only after byte validation.
                    if target['size']!=source['size'] or digest(self.path/record['source'])!=digest(self.path/record['destination']):
                        raise FileExistsError('A different file now exists: ' + record['destination'])
                    if self.info(record['source'])!=source or self.info(record['destination'])!=target:
                        raise ValueError('Files changed while checking the existing destination.')
                    record['destination_identity']=target
            return record['source']
        if record.get('move_started') and self.info(record['destination']) == self.destination_identity(record):
            return record['destination']
        raise ValueError('Planned source is missing: ' + record['source'])

    @staticmethod
    def destination_identity(record):
        return record.get('destination_identity',record['identity'])

    def move(self, record):
        if mount_identity(self.base) != self.mount:raise ValueError('Destination mount changed.')
        source = self.locate(record)
        if source == record['destination']:return
        sourcefd, name = self.parent(source)
        try:
            targetfd, target = self.parent(record['destination'], create=True)
            try:
                if identity(os.stat(name, dir_fd=sourcefd, follow_symlinks=False)) != record['identity']:
                    raise ValueError('Source changed before move: ' + source)
                if record.get('destination_identity'):
                    if self.info(record['destination'])!=record['destination_identity']:
                        raise ValueError('Verified destination changed before removing the duplicate source.')
                    os.unlink(name,dir_fd=sourcefd)
                else:
                    libc = ctypes.CDLL(None, use_errno=True)
                    if libc.renameat2(sourcefd, os.fsencode(name), targetfd, os.fsencode(target), 1):
                        raise OSError(ctypes.get_errno(), 'Could not move without overwriting: ' + source)
                os.fsync(targetfd);os.fsync(sourcefd)
            finally:os.close(targetfd)
        finally:os.close(sourcefd)
        if self.info(record['destination']) != self.destination_identity(record):
            raise ValueError('Moved file metadata changed; review required.')


def ready_groups(plan):
    attachments = {a['source_message_id']: a for a in plan.get('attachments', []) if 'source_message_id' in a}
    groups = {}
    for original in plan.get('files', []):
        if original.get('action') not in ('move', 'keep') or original.get('telegram_status') != 'matched' or not original.get('month'):
            continue
        if not original.get('identity'):raise ValueError('Run a fresh preview before applying this older plan.')
        record = json.loads(json.dumps(original))
        ids = record.get('source_message_ids', [])
        if not ids or not safe_filename(record['filename']):raise ValueError('A file needs a fresh exact Telegram comparison.')
        if record['destination'] != record['month'] + '/' + record['filename']:
            raise ValueError('Invalid monthly destination in plan.')
        for message in ids:
            item = attachments.get(message, {})
            if item.get('filename') != record['filename'] or item.get('bytes_total') != record['size']:
                raise ValueError('Plan differs from its exact Telegram file metadata.')
        key = record['month'] + '/' + volume_key(record['filename'])[0]
        groups.setdefault(key, {'key': key, 'month': record['month'], 'records': [], 'state': 'pending'})['records'].append(record)
    for group in groups.values():
        indexes = [volume_key(record['filename'])[1] for record in group['records']]
        if len(set(indexes)) != len(indexes):raise ValueError('Plan contains competing archive parts; review required.')
    return [groups[key] for key in sorted(groups)]


def regroup_saved_volumes(job):
    """Repair legacy per-part grouping in memory, before an explicit Resume.

    Preserve committed file outcomes and stable work-directory indexes even
    when a completed first part joins a pending part in another directory.
    """
    buckets = {}
    changed = False
    for index, group in enumerate(job['groups']):
        keys = {record['month'] + '/' + volume_key(record['filename'])[0] for record in group['records']}
        if len(keys) != 1:raise ValueError('Saved release has inconsistent archive parts; review required.')
        key = keys.pop()
        buckets.setdefault(key, []).append((index, group))
        changed = changed or key != group['key']
    if not changed:return False
    groups = []
    for key, sources in buckets.items():
        if len(sources) == 1:
            index, group = sources[0]
            group.setdefault('work_index', index)
            group['key'] = key
        else:
            records = []
            for _, previous in sources:
                for record in previous['records']:
                    record['recorded'] = previous['state'] == 'completed' or bool(record.get('recorded'))
                    records.append(record)
            records.sort(key=lambda record: volume_key(record['filename'])[1])
            indexes = [volume_key(record['filename'])[1] for record in records]
            if len(set(indexes)) != len(indexes):
                raise ValueError('Saved plan contains competing archive parts; review required.')
            index, pending = next(((i, g) for i, g in sources if g['state'] != 'completed'), sources[0])
            group = {'key': key, 'month': records[0]['month'], 'records': records,
                     'state': 'completed' if all(r['recorded'] for r in records) else 'pending',
                     'work_index': pending.get('work_index', index)}
        groups.append(group)
    job['groups'] = groups
    job['releases_total'] = len(groups)
    job['releases_done'] = sum(g['state'] == 'completed' for g in groups)
    job['files_done'] = sum(g['state'] == 'completed' or bool(r.get('recorded')) for g in groups for r in g['records'])
    job.update(current_release=None, phase='preparing', progress_done=0, progress_total=None)
    return True


def start_application(store, payload, resume=False):
    from folder_organizer import Organizer, ACTIVE
    with (store.root / 'data/operation.lock').open('a') as operation:
        fcntl.flock(operation, fcntl.LOCK_EX)
        organizer = Organizer(store)
        plan = organizer.status()
        if plan['state'] in ACTIVE:
            raise FileExistsError('Wait for the current folder operation to finish.')
        path = store.root / 'data/organizer-apply.json'
        previous = application_status(store)
        if resume:
            if not previous or previous['id'] != payload.get('job_id') or previous['state'] not in RETRYABLE:
                raise ValueError('No interrupted organization is available to resume.')
            job = json.loads(path.read_text())
            if regroup_saved_volumes(job):
                backup = store.root / 'data/organizer-jobs' / (job['id'] + '.before-volume-grouping.json')
                if not backup.exists():store.atomic_write(backup, json.loads(path.read_text()))
        else:
            if previous and previous['state'] in RETRYABLE:
                raise FileExistsError('Resume the unfinished organization before applying another plan.')
            if plan.get('id') != payload.get('plan_id') or plan['state'] != 'completed' or plan.get('backend') != 'cli':
                raise ValueError('Run a fresh preview and apply that completed plan.')
            if type(payload.get('extract_images')) is not bool:raise ValueError('Choose whether to extract images.')
            if store.config()['download_directory'] != plan['base']:raise ValueError('Download folder changed. Preview again.')
            catalog = json.loads((store.root / 'data/creators.json').read_text())['creators']
            if not any(c['topic_url'] == plan['topic_url'] and c['within_approved_group'] for c in catalog):
                raise ValueError('Artist is outside the approved Table of Contents.')
            groups = ready_groups(plan)
            if not groups:raise ValueError('No matched files with monthly destinations are ready to apply.')
            pinned = PinnedFolder(plan['base'], plan['creator_folder'])
            try:
                for group in groups:
                    for record in group['records']:pinned.locate(record)
                mounted = list(pinned.mount)
            finally:pinned.close()
            if previous:
                store.atomic_write(store.root / 'data/organizer-jobs' / (previous['id'] + '.json'), json.loads(path.read_text()))
            job = {k: plan[k] for k in ('base', 'creator', 'creator_folder', 'topic_url')}
            job.update(id=uuid.uuid4().hex, plan_id=plan['id'], groups=groups, mount=mounted,
                       extract_images=payload['extract_images'], started_at=datetime.now(timezone.utc).isoformat(),
                       releases_total=len(groups), releases_done=0, files_total=sum(len(g['records']) for g in groups), files_done=0,
                       moves_total=sum(r['action']=='move' for g in groups for r in g['records']), moves_done=0,
                       skipped_files=len(plan['files'])-sum(len(g['records']) for g in groups))
        job.update(state='starting', message='Preparing the saved organization plan.', heartbeat=time.time())
        store.atomic_write(path, job)
        (store.root / 'data/organizer-stop').unlink(missing_ok=True)
        with (store.root / 'data/organizer.log').open('ab') as log:
            Popen([sys.executable, str(store.root / 'organizer_apply.py'), job['id']], cwd=store.root,
                             stdout=log, stderr=log, start_new_session=True)
        return organizer.status()


class ApplyWorker:
    def __init__(self, store, job_id):
        self.store = store
        self.path = store.root / 'data/organizer-apply.json'
        self.job = json.loads(self.path.read_text())
        if self.job['id'] != job_id:raise ValueError('Organization job was replaced.')
        self.work = store.root / 'data/organizer-work' / job_id
        self.last_update = 0

    def stopped(self):return (self.store.root / 'data/organizer-stop').exists()

    def check(self):
        if self.stopped():raise InterruptedError('Organization stopped. Resume to finish the saved plan.')
        if list(mount_identity(self.job['base'])) != self.job['mount']:raise ValueError('Kronos mount changed; originals retained.')

    def save(self, **values):
        self.job.update(heartbeat=time.time(), **values)
        self.store.atomic_write(self.path, self.job)
        # Persist the directory entry too: the journal must survive a restart.
        fd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)

    def progress(self, phase, message, done=None, total=None, unit='bytes', **extra):
        if self.stopped():raise InterruptedError('Organization stopped. Resume to continue.')
        now=time.monotonic()
        if phase==self.job.get('phase') and now-self.last_update<.8:return
        self.last_update=now
        self.save(state='running',phase=phase,message=message,progress_done=done,progress_total=total,progress_unit=unit,**extra)

    def images(self, group, pinned, work):
        if not self.job['extract_images']:return None
        images_work=work/'images'
        if group.get('images') is None:
            if images_work.exists():shutil.rmtree(images_work)
            locations=[(r,pinned.path/pinned.locate(r)) for r in group['records']]
            locations.sort(key=lambda pair:volume_key(pair[0]['filename'])[1])
            first=locations[0][1]
            if volume_key(first.name)[1]>1:raise ValueError('First archive part is missing; release left in place.')
            if len({p.parent for _,p in locations})>1:
                # Only sets split between directories need local input copies.
                inputs=work/'inputs'
                if inputs.exists():shutil.rmtree(inputs)
                inputs.mkdir(parents=True)
                total=sum(r['size'] for r,_ in locations);done=0;meter=TransferMeter()
                if shutil.disk_usage(inputs).free<total+1024**3:raise ValueError('Not enough local space to stage split archive parts.')
                for record,source in locations:
                    with source.open('rb') as incoming,(inputs/source.name).open('xb') as out:
                        while block:=incoming.read(4*1024*1024):
                            out.write(block);done+=len(block);sample=meter.sample(done)
                            self.progress('preparing','Preparing archive parts from different folders',done,total,speed_bps=sample['download_speed_bps'])
                    if pinned.info(str(source.relative_to(pinned.path)))!=record['identity']:raise ValueError('Archive part changed while staging.')
                first=inputs/first.name
            started=time.monotonic()
            def progress(stage,count,total,done,expected):
                label={'listing':'Reading archive contents','checking_parts':'Checking archive parts','extracting':'Extracting images','complete':'Images extracted'}[stage]
                self.progress('extracting',label+' · '+first.name,done,expected,'percent' if stage=='checking_parts' else 'bytes',
                              images_done=count,images_total=total,elapsed_seconds=time.monotonic()-started)
            extracted=extract_images(first,images_work,progress,self.stopped)
            manifest=[]
            for image in extracted:
                relative=str(image.relative_to(images_work/'output'))
                manifest.append({'path':flat_image_name(first.name,relative),'source_path':relative,'size':image.stat().st_size,'sha256':digest(image)})
            group['images']=manifest
            self.save()
        images=group['images']
        image_total=sum(i['size'] for i in images);delivered=0;meter=TransferMeter()
        for entry in images:
            self.check()
            source=images_work/'output'/entry['source_path']
            if not source.is_file() or source.stat().st_size!=entry['size'] or digest(source)!=entry['sha256']:
                raise ValueError('Saved local image changed; organization needs review.')
            def progress(done,total):
                sample=meter.sample(delivered+done)
                self.progress('images','Moving release images to Kronos',delivered+done,image_total,speed_bps=sample['download_speed_bps'])
            def phase(value):
                self.progress('images','Verifying release images on Kronos' if value=='verifying' else 'Moving release images to Kronos',delivered,image_total)
            target,size,checksum=deliver(source,self.job['base'],self.job['creator_folder'],group['month'],entry['path'],progress,phase,subdirectories=('release_images',))
            if size!=entry['size'] or checksum!=entry['sha256']:raise ValueError('Delivered image differs from its saved manifest.')
            delivered+=entry['size']
        return images

    def execute(self):
        pinned=None
        try:
            self.check()
            pinned=PinnedFolder(self.job['base'],self.job['creator_folder'])
            # Revalidate the entire pending plan before its first mutation.
            for group in self.job['groups']:
                if group['state']=='completed':continue
                for record in group['records']:pinned.locate(record)
            self.work.mkdir(parents=True,exist_ok=True,mode=0o700)
            for index,group in enumerate(self.job['groups']):
                if group['state']=='completed':continue
                self.check()
                def waiting():self.progress('waiting','Waiting for download of '+self.job['creator']+' · '+group['month'],current_release=group['key'])
                with release_lock(self.store.root,self.job['base'],self.job['creator_folder'],group['month'],self.stopped,waiting):
                    self.save(state='running',phase='preparing',message='Organizing '+group['key'],current_release=group['key'],progress_done=0,progress_total=None)
                    work=self.work/str(group.get('work_index',index));work.mkdir(exist_ok=True)
                    images=self.images(group,pinned,work)
                    for record in group['records']:
                        self.check()
                        self.progress('moving','Moving archives into '+group['month'],self.job['moves_done'],self.job['moves_total'],'files')
                        pinned.locate(record)
                        record['move_started']=True;self.save()
                        pinned.move(record)
                        if not record.get('moved'):
                            record['moved']=True
                            if record['action']=='move':self.job['moves_done']+=1
                        self.save()
                    self.check()
                    for record in group['records']:
                        if pinned.info(record['destination'])!=pinned.destination_identity(record):raise ValueError('Organized file changed before history recording.')
                    self.progress('recording','Recording completed files in download history',self.job['files_done'],self.job['files_total'],'files')
                    self.store.history.import_organized(self.job,group['records'],images,pinned.path/group['month']/'release_images')
                    group['state']='completed'
                    self.job['releases_done']+=1
                    self.job['files_done']+=sum(not record.get('recorded') for record in group['records'])
                    for record in group['records']:record['recorded']=True
                    self.save()
                    shutil.rmtree(work)
            self.save(state='completed',phase='completed',message='Organization complete. Matched files are recorded as downloaded.',finished_at=datetime.now(timezone.utc).isoformat(),progress_done=self.job['files_done'],progress_total=self.job['files_total'],progress_unit='files')
            if self.work.exists():shutil.rmtree(self.work)
        except Exception as error:
            self.save(state='stopped' if self.stopped() else 'failed',message=str(error))
        finally:
            if pinned:pinned.close()


if __name__=='__main__':
    os.umask(0o077)
    with (ROOT/'data/organizer-worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        ApplyWorker(SubscriptionStore(ROOT),sys.argv[1]).execute()
