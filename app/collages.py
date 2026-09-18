"""Local image selection, previews and explicitly requested collage exports."""
from contextlib import contextmanager, suppress
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import uuid
from PIL import Image, ImageOps, UnidentifiedImageError

from app.collage_layout import COLORS, FORMATS, LAYOUTS, geometry, render
from app.file_delivery import deliver, digest
from app.organizer_apply import PinnedFolder, identity
from app.release_images import IMAGES, volume_key
from app.release_rules import release_month
from app.subscription_store import SubscriptionStore
from app.transfer_metrics import TransferMeter

SUPPORTED = {'.jpg', '.jpeg', '.jpe', '.png', '.webp'}
ACTIVE = ('queued', 'rendering', 'saving')
PIXEL_LIMIT = 40_000_000
BYTE_LIMIT = 64*1024**2
IMAGE_SLOTS = threading.BoundedSemaphore(2)
PREVIEW_SLOT = threading.Lock()


class CollageError(ValueError):pass


def token(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):raise CollageError('Invalid collage identifier.')
    return value


def key(value):return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:32]


def valid_release_folder(value):
    return isinstance(value,str) and bool(value) and value==value.strip() and not value.startswith('.') and not re.search(r'[\\/<>:"|?*\x00-\x1f]',value) and len(os.fsencode(value))<=255


def collage_filename(release):
    artist=release['folder'].lstrip('-! ').strip() or release['folder']
    date=release_month(release['month'])
    label=release['month']
    if not date and label.casefold().startswith(artist.casefold()+' '):label=label[len(artist):].lstrip(' -_')
    name=artist+'-'+(date or label)+'.jpg'
    if len(os.fsencode(name))>255:raise CollageError('Artist folder name is too long for a collage filename.')
    return name


class CollageStore:
    def __init__(self, store):
        self.store = store
        self.root = store.root
        self.cache = self.root/'data/collages'
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root/'data/collages.sqlite3'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY,base TEXT NOT NULL,folder TEXT NOT NULL,month TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY,release_id TEXT NOT NULL,relative TEXT NOT NULL,
                    name TEXT NOT NULL,identity TEXT NOT NULL,width INTEGER,height INTEGER,format TEXT,reason TEXT,archive TEXT);
                CREATE TABLE IF NOT EXISTS selections(release_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS previews(id TEXT PRIMARY KEY,release_id TEXT NOT NULL,recipe TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS exports(id TEXT PRIMARY KEY,release_id TEXT NOT NULL,preview_id TEXT NOT NULL,
                    filename TEXT NOT NULL,state TEXT NOT NULL,message TEXT,phase TEXT,done INTEGER,total INTEGER,
                    speed REAL,heartbeat REAL,created REAL,finished REAL,sha256 TEXT,bytes INTEGER);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def release(self, release_id):
        with self.db() as db:row = db.execute('SELECT * FROM releases WHERE id=?', (token(release_id),)).fetchone()
        if not row:raise CollageError('Open an artist and release first.')
        if row['base'] != str(Path(self.store.config()['download_directory']).resolve()):
            raise CollageError('Download folder changed. Open the release again.')
        return dict(row)

    @contextmanager
    def pinned(self, release):
        pinned = PinnedFolder(release['base'], release['folder'])
        try:yield pinned
        finally:pinned.close()

    def months(self, folder):
        base = Path(self.store.config()['download_directory']).resolve(strict=True)
        pinned = PinnedFolder(base, folder)
        try:
            months = []
            with os.scandir(pinned.fd) as entries:
                for entry in entries:
                    if not valid_release_folder(entry.name) or not entry.is_dir(follow_symlinks=False):continue
                    try:fd, _ = pinned.parent(entry.name+'/release_images/placeholder')
                    except (FileNotFoundError, NotADirectoryError):continue
                    else:os.close(fd);months.append(entry.name)
            return sorted(months,key=lambda name:(release_month(name) or '',name.casefold()),reverse=True)
        finally:pinned.close()

    def _archive_names(self, release):
        """Retain archive grouping when manifests exist; old loose images also work."""
        result = {}
        destination = Path(release['base'])/release['folder']/release['month']/'release_images'
        with self.store.history.connect() as db:
            rows = db.execute('SELECT filename,images_destination,images_manifest FROM downloads WHERE release_month=? AND images_manifest IS NOT NULL', (release_month(release['month']) or release['month'],)).fetchall()
        for row in rows:
            try:
                prefix = Path(row['images_destination']).relative_to(destination)
                entries = json.loads(row['images_manifest'])
                for image in entries:result[str(prefix/image['path'])] = volume_key(row['filename'])[0]
            except (TypeError, ValueError, KeyError):continue
        # MMF images have their own durable manifests, separate from Telegram downloads.
        mmf=self.root/'data/mmf/manager.sqlite3'
        if mmf.is_file():
            connection=sqlite3.connect(mmf.as_uri()+'?mode=ro',uri=True)
            try:
                records=[r[0] for r in connection.execute('SELECT data FROM completed')]
                prepared=[json.loads(r[0]) for r in connection.execute("SELECT value FROM state WHERE key LIKE 'prepared_images:%'")]
                records.extend(json.dumps({'repack':r}) for r in prepared)
                for raw in records:
                    item=json.loads(raw)
                    for image in item.get('repack',{}).get('images',[]):
                        try:relative=Path(image['path']).relative_to(destination)
                        except (KeyError,TypeError,ValueError):continue
                        result[str(relative)]=release['month']
            finally:connection.close()
        return result

    def index(self, folder, month):
        if not valid_release_folder(month):raise CollageError('Choose a release folder.')
        base = str(Path(self.store.config()['download_directory']).resolve(strict=True))
        release = {'id': key([base, folder, month]), 'base': base, 'folder': folder, 'month': month}
        found, warnings = [], []
        with self.pinned(release) as pinned:
            fd, _ = pinned.parent(month+'/release_images/placeholder')
            def walk(directory, prefix=PurePosixPath(), depth=0):
                if depth > 8:raise CollageError('Image folders are too deeply nested.')
                with os.scandir(directory) as entries:
                    for entry in sorted(entries, key=lambda e:e.name.casefold()):
                        relative = prefix/entry.name
                        if entry.is_symlink():warnings.append('Skipped a linked image or folder: '+str(relative));continue
                        if entry.is_dir(follow_symlinks=False):
                            child = os.open(entry.name, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=directory)
                            try:walk(child, relative, depth+1)
                            finally:os.close(child)
                        elif entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in IMAGES:
                            found.append((str(relative), identity(entry.stat(follow_symlinks=False))))
                            if len(found) > 5000:raise CollageError('This release has more than 5,000 images. Split it into smaller releases first.')
            try:walk(fd)
            finally:os.close(fd)
            archives = self._archive_names(release)
            records = []
            for relative, info in found:
                image_id = key([release['id'], relative, info])
                with self.db() as db:cached = db.execute('SELECT * FROM images WHERE id=?', (image_id,)).fetchone()
                if cached:
                    cached = dict(cached);cached['archive'] = archives.get(relative, 'Other images')
                    records.append(cached);continue
                record = {'id': image_id, 'release_id': release['id'], 'relative': relative, 'name': Path(relative).name,
                          'identity': json.dumps(info), 'width': None, 'height': None, 'format': None, 'reason': '', 'archive': archives.get(relative, 'Other images')}
                if Path(relative).suffix.lower() not in SUPPORTED:record['reason'] = 'This format is not supported for collages yet.'
                elif info['size'] > BYTE_LIMIT:record['reason'] = 'Image exceeds the 64 MiB limit.'
                else:
                    try:
                        with self._open(pinned, month+'/release_images/'+relative, info) as source, Image.open(source) as image:
                            w, h = image.size
                            if image.format not in ('JPEG','PNG','WEBP') or w*h > PIXEL_LIMIT:raise CollageError('Unsupported format or image exceeds 40 megapixels.')
                            if image.getexif().get(274) in (5,6,7,8):w, h = h, w
                            record.update(width=w, height=h, format=image.format)
                    except (OSError, ValueError, Image.DecompressionBombError) as error:record['reason'] = 'Image cannot be opened: '+str(error)[:120]
                records.append(record)
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO releases VALUES(:id,:base,:folder,:month)', release)
            for record in records:
                db.execute('INSERT OR REPLACE INTO images VALUES(:id,:release_id,:relative,:name,:identity,:width,:height,:format,:reason,:archive)', record)
            selection = db.execute('SELECT * FROM selections WHERE release_id=?', (release['id'],)).fetchone()
        draft = json.loads(selection['data']) if selection else {'images': [None]*6, 'layout': 'featured', 'shape': 'landscape', 'theme': 'dark', 'title': folder.lstrip('-! ')+' · '+month}
        return {'release': {k:release[k] for k in ('id','folder','month')}, 'images': [self.public_image(r) for r in records],
                'selection': draft, 'revision': selection['revision'] if selection else 0, 'warnings': warnings, 'exports': self.exports(release['id'])}

    @staticmethod
    def public_image(record):
        return {k:record[k] for k in ('id','name','relative','width','height','format','reason','archive')}

    @contextmanager
    def _open(self, pinned, relative, expected):
        fd, name = pinned.parent(relative)
        try:source = os.open(name, os.O_RDONLY|os.O_NOFOLLOW, dir_fd=fd)
        finally:os.close(fd)
        with os.fdopen(source, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or identity(os.fstat(stream.fileno())) != expected:
                raise CollageError('An image changed. Refresh the release and select it again.')
            yield stream
            if identity(os.fstat(stream.fileno())) != expected or pinned.info(relative) != expected:
                raise CollageError('An image changed while it was being read. Refresh the release.')

    def image_record(self, image_id):
        with self.db() as db:row = db.execute('SELECT * FROM images WHERE id=?', (token(image_id),)).fetchone()
        if not row or row['reason']:raise CollageError('This image is unavailable for collages.')
        return dict(row)

    def source(self, record, checksum=None):
        release = self.release(record['release_id'])
        with self.pinned(release) as pinned, self._open(pinned, release['month']+'/release_images/'+record['relative'], json.loads(record['identity'])) as source:
            data = source.read(BYTE_LIMIT+1)
        if len(data) > BYTE_LIMIT:raise CollageError('Image exceeds the size limit.')
        actual = hashlib.sha256(data).hexdigest()
        if checksum is not None and actual != checksum:raise CollageError('Selected image content changed. Generate a new preview.')
        return data, actual

    def thumbnail(self, image_id, large=False):
        record = self.image_record(image_id)
        release = self.release(record['release_id'])
        with self.pinned(release) as pinned:
            if pinned.info(release['month']+'/release_images/'+record['relative']) != json.loads(record['identity']):raise CollageError('Image changed. Refresh the release.')
        output = self.cache/(record['id']+('-large.jpg' if large else '-thumb.jpg'))
        if not output.exists():
            with IMAGE_SLOTS:
                data, _ = self.source(record)
                with Image.open(io.BytesIO(data)) as original:
                    image = ImageOps.exif_transpose(original)
                    try:
                        image.thumbnail((1600,1600) if large else (360,300), Image.Resampling.LANCZOS)
                        rgba = image.convert('RGBA');result = Image.new('RGB', image.size, '#eeeaf2')
                        result.paste(rgba, (0,0), rgba)
                        temporary = output.with_name(output.name+'.'+uuid.uuid4().hex)
                        try:result.save(temporary, 'JPEG', quality=88);os.replace(temporary, output)
                        finally:temporary.unlink(missing_ok=True);result.close();rgba.close()
                    finally:image.close()
        return output.read_bytes()

    def validate_selection(self, payload, minimum=0):
        release = self.release(payload.get('release_id'))
        ids = payload.get('images')
        if not isinstance(ids, list) or not 2 <= len(ids) <= 9:raise CollageError('Choose between two and nine collage slots.')
        filled = [i for i in ids if i is not None]
        if len(set(map(str,filled))) != len(filled) or (minimum and len(filled)!=len(ids)):raise CollageError('Fill every collage slot with a different image.')
        records = [self.image_record(i) for i in filled]
        if any(r['release_id'] != release['id'] for r in records):raise CollageError('Every selected image must belong to this release.')
        if payload.get('layout') not in LAYOUTS or payload.get('shape') not in FORMATS or payload.get('theme') not in COLORS:raise CollageError('Choose a valid layout, output size and background.')
        title = payload.get('title', '')
        if not isinstance(title,str) or len(title)>120 or re.search(r'[\x00-\x1f\x7f]', title):raise CollageError('Use a title of up to 120 characters.')
        settings = {k:payload[k] for k in ('layout','shape','theme')};settings['title'] = title.strip()
        return release, records, settings

    def layout(self, payload):
        _, records, settings = self.validate_selection(payload)
        records = {r['id']:r for r in records}
        sizes = [(records[i]['width'],records[i]['height']) if i else ((3,4) if n==0 and settings['layout']=='featured' else (4,3)) for n,i in enumerate(payload['images'])]
        cells = geometry(sizes, settings['layout'], settings['shape'], bool(settings['title']))
        return {'size': FORMATS[settings['shape']], 'cells': [{'index':i,'rect':rect} for i,rect in cells]}

    def save_selection(self, payload):
        release, _, settings = self.validate_selection(payload)
        data = dict(settings, images=payload['images'])
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM selections WHERE release_id=?', (release['id'],)).fetchone()
            revision = row['revision'] if row else 0
            if payload.get('revision') != revision:raise FileExistsError('This selection changed in another tab. Reopen the release to load it.')
            db.execute('INSERT OR REPLACE INTO selections VALUES(?,?,?)', (release['id'], revision+1, json.dumps(data)))
        return {'revision': revision+1}

    def _sources(self, images, snapshot=None):
        sources = []
        for i, record in enumerate(images):
            def opener(record=record, i=i):
                data, checksum = self.source(record, snapshot[i]['sha256'] if snapshot else None)
                if not snapshot:record['sha256'] = checksum
                return io.BytesIO(data)
            sources.append(dict(record, open=opener))
        return sources

    def preview(self, payload):
        release, records, settings = self.validate_selection(payload, minimum=2)
        if not PREVIEW_SLOT.acquire(timeout=20):raise CollageError('Another preview is being prepared. Try again shortly.')
        preview_id = uuid.uuid4().hex;output = self.cache/(preview_id+'-preview.jpg')
        try:
            with IMAGE_SLOTS:report = render(self._sources(records), settings, output, preview=True)
            recipe = {'release_id': release['id'], 'settings': settings, 'images': [{'id':r['id'],'sha256':r['sha256']} for r in records]}
            with self.db() as db:db.execute('INSERT INTO previews VALUES(?,?,?,?)', (preview_id, release['id'], json.dumps(recipe), time.time()))
            return dict(report, id=preview_id, url='/api/collages/preview/'+preview_id, output_size=FORMATS[settings['shape']])
        except Exception:
            output.unlink(missing_ok=True);raise
        finally:PREVIEW_SLOT.release()

    def preview_data(self, preview_id):
        with self.db() as db:row = db.execute('SELECT * FROM previews WHERE id=?', (token(preview_id),)).fetchone()
        if not row:raise CollageError('Preview unavailable. Generate it again.')
        self.release(row['release_id'])
        return dict(row)

    def export_status(self, export_id):
        with self.db() as db:row = db.execute('SELECT * FROM exports WHERE id=?', (token(export_id),)).fetchone()
        if not row:raise CollageError('Collage export not found.')
        result = dict(row)
        result['backup_available']=bool(result['sha256'] and self.version_path(result['sha256']).exists())
        if result['state'] in ACTIVE and time.time()-result['heartbeat']>15:
            with (self.cache/'worker.lock').open('a') as lock:
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:pass
                else:
                    self.update_export(export_id, state='interrupted', message='Export interrupted. Retry this saved export.')
                    result.update(state='interrupted', message='Export interrupted. Retry this saved export.')
        return result

    def exports(self, release_id):
        with self.db() as db:ids = [r['id'] for r in db.execute('SELECT id FROM exports WHERE release_id=? ORDER BY created DESC LIMIT 20', (token(release_id),))]
        return [self.export_status(i) for i in ids]

    def update_export(self, export_id, **values):
        values['heartbeat'] = time.time()
        with self.db() as db:db.execute('UPDATE exports SET '+','.join(k+'=?' for k in values)+' WHERE id=?', (*values.values(), export_id))

    def start_export(self, payload):
        with (self.cache/'operation.lock').open('a') as operation:
            fcntl.flock(operation, fcntl.LOCK_EX)
            with self.db() as db:ids = [r['id'] for r in db.execute("SELECT id FROM exports WHERE state IN ('queued','rendering','saving')")]
            if any(self.export_status(i)['state'] in ACTIVE for i in ids):raise FileExistsError('A collage is being saved. Wait for it to finish.')
            if payload.get('retry_id'):
                job = self.export_status(payload['retry_id'])
                if job['state'] not in ('failed','interrupted'):raise CollageError('Only an unfinished collage export can be retried.')
                self.preview_data(job['preview_id']);export_id = job['id']
                self.update_export(export_id, filename=collage_filename(self.release(job['release_id'])), state='queued', message='Retrying saved collage.', phase='preparing', done=0, total=None)
            else:
                preview = self.preview_data(payload.get('preview_id'))
                release = self.release(preview['release_id']);export_id = uuid.uuid4().hex
                filename = collage_filename(release)
                with self.db() as db:db.execute('INSERT INTO exports(id,release_id,preview_id,filename,state,message,phase,done,heartbeat,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (export_id,release['id'],preview['id'],filename,'queued','Preparing full-size collage.','preparing',0,time.time(),time.time()))
            try:
                with (self.cache/'worker.log').open('ab') as log:
                    subprocess.Popen([sys.executable,'-m','app.collages','export',export_id],cwd=self.root,stdout=log,stderr=log,start_new_session=True)
            except OSError:
                self.update_export(export_id, state='failed', message='Could not start collage export. Retry when the service is available.');raise
        return self.export_status(export_id)

    def version_path(self, checksum):
        if not isinstance(checksum,str) or not re.fullmatch('[a-f0-9]{64}',checksum):raise CollageError('Invalid saved collage checksum.')
        return self.cache/'versions'/(checksum+'.jpg')

    def verified_data(self, pinned, relative, checksum=None, size=None):
        info=pinned.info(relative)
        if not info or info['size']>32*1024**2 or (size is not None and info['size']!=size):raise CollageError('Saved collage is missing or changed.')
        with self._open(pinned,relative,info) as source:data=source.read(32*1024**2+1)
        if len(data)!=info['size'] or (checksum is not None and hashlib.sha256(data).hexdigest()!=checksum):raise CollageError('Saved collage is missing or changed.')
        return data,info

    def keep_version(self, data, checksum):
        path=self.version_path(checksum);path.parent.mkdir(exist_ok=True,mode=0o700)
        if path.exists() and digest(path)==checksum:return
        temporary=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            if digest(temporary)!=checksum:raise CollageError('Could not verify the previous collage backup.')
            os.replace(temporary,path)
            fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
        finally:temporary.unlink(missing_ok=True)

    def deliver_export(self, output, release, job, progress, phase):
        """Back up a recognized collage before replacing it with a verified save.

        Both renames reject collisions. A recovery journal covers interruption
        between moving the previous file aside and delivering the new version.
        """
        journal=self.cache/(job['id']+'-replacement.json')
        relative=release['month']+'/'+job['filename'];checksum=digest(output)
        with self.pinned(release) as pinned:
            previous=json.loads(journal.read_text()) if journal.exists() else None
            if previous and pinned.info(previous['move']['destination']):
                if pinned.info(relative) is None:
                    old=previous['move'];self.verified_data(pinned,old['destination'],previous['sha256'],old['identity']['size'])
                    pinned.move({'source':old['destination'],'destination':relative,'identity':old['identity']})
                else:
                    # A crash after delivery can be completed without replacing again.
                    self.verified_data(pinned,relative,checksum,output.stat().st_size)
                    return deliver(output,release['base'],release['folder'],release['month'],job['filename'],progress,phase,release_directory=release['month'])
            info=pinned.info(relative)
            if info:
                data,info=self.verified_data(pinned,relative);old_checksum=hashlib.sha256(data).hexdigest()
                if old_checksum!=checksum:
                    with self.db() as db:known=db.execute("SELECT id FROM exports WHERE release_id=? AND filename=? AND state='completed' AND sha256=? AND bytes=? LIMIT 1",(release['id'],job['filename'],old_checksum,len(data))).fetchone()
                    if not known:raise CollageError('The existing file does not match a previously saved collage. It was kept unchanged.')
                    self.keep_version(data,old_checksum)
                    move={'source':relative,'destination':release['month']+'/.collage-'+job['id']+'-previous.jpg','identity':info,'move_started':True}
                    previous={'move':move,'sha256':old_checksum};self.store.atomic_write(journal,previous)
                    pinned.move(move)
            try:return deliver(output,release['base'],release['folder'],release['month'],job['filename'],progress,phase,release_directory=release['month'])
            except Exception:
                if previous and pinned.info(relative) is None and pinned.info(previous['move']['destination']):
                    old=previous['move'];self.verified_data(pinned,old['destination'],previous['sha256'],old['identity']['size'])
                    pinned.move({'source':old['destination'],'destination':relative,'identity':old['identity']})
                raise

    def finish_replacement(self, release, job):
        journal=self.cache/(job['id']+'-replacement.json')
        if not journal.exists():return
        previous=json.loads(journal.read_text());old=previous['move']
        with self.pinned(release) as pinned:
            if pinned.info(old['destination']):
                self.verified_data(pinned,old['destination'],previous['sha256'],old['identity']['size'])
                if digest(self.version_path(previous['sha256']))!=previous['sha256']:raise CollageError('Previous collage backup needs review.')
                fd,name=pinned.parent(old['destination'])
                try:
                    if identity(os.stat(name,dir_fd=fd,follow_symlinks=False))!=old['identity']:raise CollageError('Previous collage changed before cleanup.')
                    os.unlink(name,dir_fd=fd);os.fsync(fd)
                finally:os.close(fd)
        journal.unlink()

    def run_export(self, export_id):
        output = self.cache/(token(export_id)+'-output.jpg')
        try:
            job = self.export_status(export_id)
            if job['state'] not in ACTIVE:raise CollageError('This export is not waiting to run.')
            recipe = json.loads(self.preview_data(job['preview_id'])['recipe'])
            release = self.release(job['release_id'])
            records = [self.image_record(i['id']) for i in recipe['images']]
            self.update_export(export_id, state='rendering', message='Composing full-size collage.', phase='rendering', done=0, total=None)
            if not output.exists() or not job['sha256'] or digest(output)!=job['sha256']:
                temporary = output.with_suffix('.tmp')
                try:
                    with IMAGE_SLOTS:render(self._sources(records, recipe['images']), recipe['settings'], temporary)
                    os.replace(temporary, output)
                finally:temporary.unlink(missing_ok=True)
                self.update_export(export_id, sha256=digest(output), bytes=output.stat().st_size)
            else:
                for record, proof in zip(records, recipe['images']):self.source(record, proof['sha256'])
            meter=TransferMeter();last=0
            def progress(done,total):
                nonlocal last
                if time.monotonic()-last<.3 and done!=total:return
                last=time.monotonic();speed=meter.sample(done)['download_speed_bps']
                self.update_export(export_id,state='saving',message='Saving collage to the release folder.',phase='saving',done=done,total=total,speed=speed)
            def phase(value):
                self.update_export(export_id,state='saving',message='Verifying saved collage.' if value=='verifying' else 'Saving collage to the release folder.',phase=value,total=output.stat().st_size)
            target,size,checksum=self.deliver_export(output,release,job,progress,phase)
            self.update_export(export_id,state='completed',message='Collage saved in '+release['folder']+'/'+release['month']+'/',phase='completed',done=size,total=size,sha256=checksum,bytes=size,finished=time.time())
            with suppress(OSError,ValueError):self.finish_replacement(release,job)
            with suppress(OSError):output.unlink(missing_ok=True)
        except Exception as error:
            self.update_export(export_id,state='failed',message=str(error)[:500],speed=None)

    def result(self, export_id):
        job = self.export_status(export_id)
        if job['state']!='completed':raise CollageError('This collage has not finished saving.')
        release = self.release(job['release_id'])
        backup=self.version_path(job['sha256'])
        if backup.exists() and backup.stat().st_size==job['bytes']:
            data=backup.read_bytes()
            if hashlib.sha256(data).hexdigest()==job['sha256']:return data,job['filename']
        with self.pinned(release) as pinned:
            # Keep older saved versions accessible at their original location.
            for relative in (release['month']+'/'+job['filename'], release['month']+'/release_collages/'+job['filename']):
                info=pinned.info(relative)
                if not info or info['size']!=job['bytes']:continue
                with self._open(pinned,relative,info) as source:data=source.read(32*1024**2)
                if hashlib.sha256(data).hexdigest()==job['sha256']:return data,job['filename']
        raise CollageError('Saved collage is missing or changed.')


if __name__=='__main__':
    os.umask(0o077)
    os.nice(10)
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
    store=CollageStore(SubscriptionStore(Path(__file__).resolve().parent.parent))
    if len(sys.argv)!=3 or sys.argv[1]!='export':raise SystemExit('Expected export identifier.')
    with (store.cache/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        store.run_export(token(sys.argv[2]))
