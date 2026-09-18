"""Durable Telegram attachment identities and transfer history, independent of paths."""
import sqlite3
import hashlib
import json
from pathlib import Path
import re
from contextlib import contextmanager
from app.source_scope import load_source
from app.release_images import image_warnings as actual_image_warnings

class DownloadHistory:
    def __init__(self, path, source=None):
        self.path=Path(path)
        self._source=source
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self.connect() as db:
            # Organizer and downloader may start together during an upgrade.
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY, source_chat_id TEXT NOT NULL,
                source_message_id INTEGER NOT NULL CHECK(source_message_id>0),
                attachment_index INTEGER NOT NULL DEFAULT 0 CHECK(attachment_index>=0),
                creator TEXT NOT NULL, topic_url TEXT NOT NULL, filename TEXT NOT NULL,
                batch_id TEXT NOT NULL DEFAULT 'default',
                release_month TEXT, original_destination TEXT,
                state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','downloading','downloaded','failed','paused','needs_review')),
                bytes_downloaded INTEGER NOT NULL DEFAULT 0 CHECK(bytes_downloaded>=0),
                bytes_total INTEGER CHECK(bytes_total>=0), sha256 TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT,
                error TEXT, UNIQUE(source_chat_id,source_message_id,attachment_index))''')

            columns={row['name'] for row in db.execute('PRAGMA table_info(downloads)')}
            for name,kind in {'download_started_at':'TEXT','download_finished_at':'TEXT','download_seconds':'REAL','download_speed_bps':'REAL','download_average_bps':'REAL','bytes_total_estimate':'INTEGER','move_seconds':'REAL','move_average_bps':'REAL','image_count':'INTEGER','images_destination':'TEXT','images_manifest':'TEXT','image_warnings':"TEXT NOT NULL DEFAULT '[]'",'image_status':'TEXT','images_extracted_at':'TEXT','origin':"TEXT NOT NULL DEFAULT 'download'"}.items():
                if name not in columns:db.execute(f'ALTER TABLE downloads ADD COLUMN {name} {kind}')
            if 'images_extracted_at' not in columns:
                db.execute("""UPDATE downloads SET images_extracted_at=COALESCE(completed_at,CURRENT_TIMESTAMP),
                    image_status=CASE WHEN image_warnings='[]' THEN 'complete' ELSE 'complete_with_warnings' END
                    WHERE images_manifest IS NOT NULL""")
            db.execute('''CREATE TABLE IF NOT EXISTS image_extractions (
                identity TEXT PRIMARY KEY, topic_url TEXT NOT NULL, archives TEXT NOT NULL,
                destination TEXT NOT NULL, manifest TEXT NOT NULL, warnings TEXT NOT NULL,
                state TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            for name in ('ignored_at','ignore_reason'):
                if name not in columns:db.execute(f'ALTER TABLE downloads ADD COLUMN {name} TEXT')
            db.execute('''CREATE TABLE IF NOT EXISTS organized_files (
                operation_id TEXT NOT NULL, topic_url TEXT NOT NULL, source_path TEXT NOT NULL,
                destination TEXT NOT NULL, filename TEXT NOT NULL, bytes_total INTEGER NOT NULL,
                release_month TEXT NOT NULL, message_ids TEXT NOT NULL, images_manifest TEXT,
                completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(operation_id,source_path))''')

    @property
    def source(self):
        if self._source is None:self._source=load_source(self.path.parent.parent)
        return self._source

    def image_identity(self, topic, records, destination):
        if self.source.topic_id(topic) is None:raise ValueError('Extraction is outside the approved group.')
        archives=sorted({(r['filename'],r['size']) for r in records})
        if not archives or any(not isinstance(name,str) or type(size) is not int or size<0 for name,size in archives):
            raise ValueError('Exact archive filenames and sizes are required.')
        destination=str(destination)
        if not Path(destination).is_absolute():raise ValueError('An absolute image destination is required.')
        # Parts added later and changed sizes form a new extraction. Different
        # destinations must receive their own images; no NAS reads are needed.
        identity_parts=[topic,archives,destination]
        versions=sorted({r['archive_version'] for r in records if r.get('archive_version')})
        if versions:identity_parts.append(versions)
        value=json.dumps(identity_parts,ensure_ascii=False,separators=(',',':'))
        return hashlib.sha256(value.encode()).hexdigest(),json.dumps(archives),destination

    def image_extraction(self, topic, records, destination):
        identity,archives,destination=self.image_identity(topic,records,destination)
        with self.connect() as db:
            row=db.execute('SELECT * FROM image_extractions WHERE identity=?',(identity,)).fetchone()
            if row:
                warnings=actual_image_warnings(json.loads(row['warnings']))
                return {'images':json.loads(row['manifest']),'warnings':warnings,
                        'state':'complete_with_warnings' if warnings else 'complete','completed_at':row['completed_at']}
            if any(r.get('archive_version') for r in records):return None
            # Existing installations already saved successful manifests. Reuse
            # those too, but only when every archive has the same saved result.
            saved=[]
            for name,size in json.loads(archives):
                row=db.execute('''SELECT * FROM downloads WHERE topic_url=? AND filename=? AND bytes_total=?
                    AND images_destination=? AND images_manifest IS NOT NULL AND state='downloaded'
                    ORDER BY images_extracted_at DESC LIMIT 1''',(topic,name,size,destination)).fetchone()
                if not row:return None
                saved.append(row)
            images=json.loads(saved[0]['images_manifest'])
            if not isinstance(images,list) or any(json.loads(r['images_manifest'])!=images for r in saved):return None
            warnings=actual_image_warnings(list(dict.fromkeys(w for r in saved for w in json.loads(r['image_warnings']))))
            return {'images':images,'warnings':warnings,'state':'complete_with_warnings' if warnings else 'complete',
                    'completed_at':saved[0]['images_extracted_at'] or saved[0]['completed_at']}

    def record_image_extraction(self, topic, records, images, destination, *, warnings=()):
        """Called only after every extracted image has been delivered and verified."""
        identity,archives,destination=self.image_identity(topic,records,destination)
        warnings=actual_image_warnings(warnings)
        state='complete_with_warnings' if warnings else 'complete'
        manifest=json.dumps(images);warning_json=json.dumps(list(warnings))
        with self.connect() as db:
            db.execute('''INSERT INTO image_extractions(identity,topic_url,archives,destination,manifest,warnings,state)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(identity) DO NOTHING''',
                (identity,topic,archives,destination,manifest,warning_json,state))
            receipt=db.execute('SELECT * FROM image_extractions WHERE identity=?',(identity,)).fetchone()
            if json.loads(receipt['manifest'])!=images or actual_image_warnings(json.loads(receipt['warnings']))!=warnings:
                raise ValueError('Extraction differs from the saved completed result.')
            for name,size in json.loads(archives):
                suffix='';values=[]
                if any(r.get('archive_version') for r in records):
                    ids=sorted({mid for r in records if r['filename']==name and r['size']==size for mid in r['source_message_ids']})
                    suffix=' AND source_message_id IN ('+','.join('?' for _ in ids)+')';values=ids
                db.execute('''UPDATE downloads SET image_count=?,images_destination=?,images_manifest=?,image_warnings=?,
                    image_status=?,images_extracted_at=? WHERE topic_url=? AND filename=? AND bytes_total=?'''+suffix,
                    (len(images),destination,manifest,warning_json,state,receipt['completed_at'],topic,name,size,*values))
        return {'images':images,'warnings':list(warnings),'state':state,'completed_at':receipt['completed_at']}

    def import_organized(self, job, records, images, images_destination, *, image_warnings=()):
        """Import exact CLI identities after NAS renames, without inventing transfer checksums."""
        import json
        image_warnings=actual_image_warnings(image_warnings)
        chat=str(self.source.chat_id)
        if self.source.topic_id(job['topic_url']) is None:
            raise ValueError('Organization is outside the approved group.')
        with self.connect() as db:
            for record in records:
                ids=record['source_message_ids']
                if not ids or any(type(i) is not int or i<=0 for i in ids):raise ValueError('Exact Telegram message identities are required.')
                destination=str(Path(job['base'])/job['creator_folder']/record['destination'])
                for message in ids:
                    old=db.execute('SELECT * FROM downloads WHERE source_chat_id=? AND source_message_id=? AND attachment_index=0',(chat,message)).fetchone()
                    if old and (old['topic_url']!=job['topic_url'] or old['filename']!=record['filename'] or old['bytes_total'] not in (None,record['size'])):
                        raise ValueError('Saved attachment identity differs from this organization plan.')
                    if not old:
                        db.execute('''INSERT INTO downloads(source_chat_id,source_message_id,creator,topic_url,filename,bytes_total,
                            release_month,batch_id,origin) VALUES(?,?,?,?,?,?,?,?,?)''',
                            (chat,message,job['creator'],job['topic_url'],record['filename'],record['size'],record['month'],'organizer-'+job['id'],'organizer'))
                    db.execute('''UPDATE downloads SET state='downloaded',bytes_total=?,bytes_downloaded=?,release_month=?,
                        original_destination=?,completed_at=CURRENT_TIMESTAMP,error=NULL,origin=?,sha256=?
                        WHERE source_chat_id=? AND source_message_id=? AND attachment_index=0 AND state!='downloaded' ''',
                        (record['size'],record['size'],record['month'],destination,
                         'download' if record.get('repair_download') else 'organizer',record.get('repair_download',{}).get('sha256'),chat,message))
                    if images is not None:
                        db.execute('''UPDATE downloads SET image_count=?,images_destination=?,images_manifest=?,image_warnings=?,
                            image_status=?,images_extracted_at=COALESCE(images_extracted_at,CURRENT_TIMESTAMP)
                            WHERE source_chat_id=? AND source_message_id=? AND attachment_index=0''',
                            (len(images),str(images_destination),json.dumps(images),json.dumps(list(image_warnings)),
                             'complete_with_warnings' if image_warnings else 'complete',chat,message))
                db.execute('''INSERT INTO organized_files(operation_id,topic_url,source_path,destination,filename,bytes_total,
                    release_month,message_ids,images_manifest) VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(operation_id,source_path) DO NOTHING''',
                    (job['id'],job['topic_url'],record['source'],destination,record['filename'],record['size'],record['month'],json.dumps(ids),json.dumps(images) if images is not None else None))

    def record_transfer(self,item_id,done,total,metrics,estimate=None):
        names=('download_started_at','download_finished_at','download_seconds','download_speed_bps','download_average_bps')
        with self.connect() as db:
            db.execute("UPDATE downloads SET state='downloading',bytes_downloaded=?,bytes_total=?,bytes_total_estimate=COALESCE(?,bytes_total_estimate),"+
                ','.join(name+'=?' for name in names)+" WHERE id=? AND state!='downloaded'",
                (done,total,estimate,*(metrics[name] for name in names),item_id))

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def register(self, *, source_message_id, creator, topic_url, filename,
                 source_chat_id=None, attachment_index=0, bytes_total=None, release_month=None, batch_id='default'):
        if source_chat_id is None:source_chat_id=self.source.chat_id
        if str(source_chat_id)!=str(self.source.chat_id) or self.source.topic_id(topic_url) is None:
            raise ValueError('Attachment is outside the approved group.')
        with self.connect() as db:
            db.execute('''INSERT INTO downloads(source_chat_id,source_message_id,attachment_index,creator,topic_url,filename,bytes_total,release_month,batch_id)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_chat_id,source_message_id,attachment_index) DO NOTHING''',
                (str(source_chat_id),source_message_id,attachment_index,creator,topic_url,filename,bytes_total,release_month,batch_id))
            return dict(db.execute('SELECT * FROM downloads WHERE source_chat_id=? AND source_message_id=? AND attachment_index=?',
                (str(source_chat_id),source_message_id,attachment_index)).fetchone())

    def was_downloaded(self, source_message_id, attachment_index=0, source_chat_id=None):
        # Deliberately do not check whether the destination still exists.
        if source_chat_id is None:source_chat_id=self.source.chat_id
        with self.connect() as db:
            row=db.execute('SELECT state FROM downloads WHERE source_chat_id=? AND source_message_id=? AND attachment_index=?',
                (str(source_chat_id),source_message_id,attachment_index)).fetchone()
            return bool(row and row['state']=='downloaded')

    def should_skip(self, source_message_id, attachment_index=0):
        """Completed downloads and explicitly ignored corrupt sources stay skipped."""
        with self.connect() as db:
            row=db.execute('SELECT state,ignored_at FROM downloads WHERE source_chat_id=? AND source_message_id=? AND attachment_index=?',
                           (str(self.source.chat_id),source_message_id,attachment_index)).fetchone()
            return bool(row and (row['state']=='downloaded' or row['ignored_at']))

    def set_progress(self, item_id, bytes_downloaded):
        with self.connect() as db:
            row=db.execute('SELECT * FROM downloads WHERE id=?',(item_id,)).fetchone()
            if not row:raise ValueError('Unknown download.')
            if row['state']=='downloaded':raise ValueError('Completed history cannot be reset by a queue update.')
            if bytes_downloaded<0 or (row['bytes_total'] is not None and bytes_downloaded>row['bytes_total']):raise ValueError('Invalid byte count.')
            db.execute("UPDATE downloads SET state='downloading',bytes_downloaded=?,error=NULL WHERE id=?",(bytes_downloaded,item_id))

    def complete(self, item_id, *, destination, verified_size, sha256):
        if not re.fullmatch('[0-9a-f]{64}',sha256):raise ValueError('A verified SHA-256 checksum is required.')
        if not Path(destination).is_absolute() or verified_size<0:raise ValueError('Invalid completed-file metadata.')
        with self.connect() as db:
            row=db.execute('SELECT * FROM downloads WHERE id=?',(item_id,)).fetchone()
            if not row:raise ValueError('Unknown download.')
            if row['state']=='downloaded':return  # A retry cannot erase historical metadata.
            if row['bytes_total'] is not None and verified_size!=row['bytes_total']:raise ValueError('Incomplete file size.')
            db.execute("""UPDATE downloads SET state='downloaded',bytes_downloaded=?,bytes_total=?,sha256=?,
                original_destination=?,completed_at=CURRENT_TIMESTAMP,error=NULL WHERE id=?""",
                (verified_size,verified_size,sha256,str(destination),item_id))

    def complete_release(self, records, images, images_destination):
        """Commit every volume together, only after archives and images are verified."""
        import json
        with self.connect() as db:
            for record in records:
                if not re.fullmatch('[0-9a-f]{64}',record['sha256']) or not Path(record['destination']).is_absolute():
                    raise ValueError('Verified destination metadata is required.')
                row=db.execute('SELECT * FROM downloads WHERE id=?',(record['id'],)).fetchone()
                if not row or (row['bytes_total'] is not None and row['bytes_total']!=record['size']):
                    raise ValueError('Incomplete release file size.')
                db.execute("""UPDATE downloads SET state='downloaded',bytes_downloaded=?,bytes_total=?,sha256=?,
                    original_destination=?,completed_at=CURRENT_TIMESTAMP,error=NULL,move_seconds=?,move_average_bps=?,
                    image_count=?,images_destination=?,images_manifest=?,image_status=COALESCE(image_status,'complete'),
                    images_extracted_at=COALESCE(images_extracted_at,CURRENT_TIMESTAMP) WHERE id=? AND state!='downloaded'""",
                    (record['size'],record['size'],record['sha256'],record['destination'],record['move_seconds'],record['move_average_bps'],
                     len(images),str(images_destination),json.dumps(images),record['id']))

    def batch_outcome(self,batch_id):
        with self.connect() as db:
            row=db.execute("SELECT count(*) AS total_files,coalesce(sum(state='downloaded'),0) AS completed_files FROM downloads WHERE batch_id=? AND ignored_at IS NULL",(batch_id,)).fetchone()
            ignored=db.execute('SELECT count(*) FROM downloads WHERE batch_id=? AND ignored_at IS NOT NULL',(batch_id,)).fetchone()[0]
        return {**dict(row),'unfinished_files':row['total_files']-row['completed_files'],'ignored_count':ignored}

    def run_warnings(self,batch_id):
        with self.connect() as db:
            rows=db.execute("SELECT creator,filename,state,error,image_warnings FROM downloads WHERE batch_id=? AND ignored_at IS NULL ORDER BY id",(batch_id,)).fetchall()
        warnings=[]
        for row in rows:
            if row['state']!='downloaded' and row['error']:
                warnings.append(row['creator']+' · '+row['filename']+': '+row['error'])
            if row['state']=='downloaded':
                warnings.extend(row['creator']+' · '+w for w in actual_image_warnings(json.loads(row['image_warnings'])))
        return list(dict.fromkeys(warnings))

    def queue(self,batch_id=None):
        with self.connect() as db:
            batch={'batch_id':batch_id} if batch_id else db.execute('SELECT batch_id FROM downloads ORDER BY id DESC LIMIT 1').fetchone()
            rows=[dict(r) for r in db.execute('SELECT * FROM downloads WHERE batch_id=? ORDER BY id',(batch['batch_id'],))] if batch else []
            completed=db.execute("SELECT count(*) FROM downloads WHERE state='downloaded'").fetchone()[0]
        ignored=[{k:r[k] for k in ('id','creator','filename','ignore_reason','ignored_at')} for r in rows if r.get('ignored_at')]
        rows=[r for r in rows if not r.get('ignored_at')]
        for row in rows:
            row['progress_total']=row['bytes_total'] if row['bytes_total'] is not None else row['bytes_total_estimate']
            row['total_is_estimate']=row['bytes_total'] is None and row['bytes_total_estimate'] is not None
        return {'files':[r for r in rows if r['state']!='downloaded'],'total_files':len(rows),'completed_files':sum(r['state']=='downloaded' for r in rows),
                'bytes_downloaded':sum(r['bytes_downloaded'] for r in rows),
                'bytes_total':sum(r['bytes_total'] for r in rows) if rows and all(r['bytes_total'] is not None for r in rows) else None,
                'progress_total':sum(r['progress_total'] for r in rows) if rows and all(r['progress_total'] is not None for r in rows) else None,
                'total_is_estimate':any(r['total_is_estimate'] for r in rows),
                'recent_completed':[r for r in rows if r['state']=='downloaded'][-10:],
                'history_completed':completed,'ignored_files':ignored}
