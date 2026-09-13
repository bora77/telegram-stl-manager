"""Durable Telegram attachment identities and transfer history, independent of paths."""
import sqlite3
from pathlib import Path
import re
from contextlib import contextmanager
from source_scope import load_source

class DownloadHistory:
    def __init__(self, path, source=None):
        self.path=Path(path)
        self._source=source
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self.connect() as db:
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
            for name,kind in {'download_started_at':'TEXT','download_finished_at':'TEXT','download_seconds':'REAL','download_speed_bps':'REAL','download_average_bps':'REAL','bytes_total_estimate':'INTEGER','move_seconds':'REAL','move_average_bps':'REAL','image_count':'INTEGER','images_destination':'TEXT','images_manifest':'TEXT','image_warnings':"TEXT NOT NULL DEFAULT '[]'",'origin':"TEXT NOT NULL DEFAULT 'download'"}.items():
                if name not in columns:db.execute(f'ALTER TABLE downloads ADD COLUMN {name} {kind}')
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

    def import_organized(self, job, records, images, images_destination, *, image_warnings=()):
        """Import exact CLI identities after NAS renames, without inventing transfer checksums."""
        import json
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
                        db.execute('''UPDATE downloads SET image_count=?,images_destination=?,images_manifest=?,image_warnings=?
                            WHERE source_chat_id=? AND source_message_id=? AND attachment_index=0''',
                            (len(images),str(images_destination),json.dumps(images),json.dumps(list(image_warnings)),chat,message))
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
                    image_count=?,images_destination=?,images_manifest=? WHERE id=? AND state!='downloaded'""",
                    (record['size'],record['size'],record['sha256'],record['destination'],record['move_seconds'],record['move_average_bps'],
                     len(images),str(images_destination),json.dumps(images),record['id']))

    def queue(self,batch_id=None):
        with self.connect() as db:
            batch={'batch_id':batch_id} if batch_id else db.execute('SELECT batch_id FROM downloads ORDER BY id DESC LIMIT 1').fetchone()
            rows=[dict(r) for r in db.execute('SELECT * FROM downloads WHERE batch_id=? ORDER BY id',(batch['batch_id'],))] if batch else []
            completed=db.execute("SELECT count(*) FROM downloads WHERE state='downloaded'").fetchone()[0]
        for row in rows:
            row['progress_total']=row['bytes_total'] if row['bytes_total'] is not None else row['bytes_total_estimate']
            row['total_is_estimate']=row['bytes_total'] is None and row['bytes_total_estimate'] is not None
        return {'files':[r for r in rows if r['state']!='downloaded'],'total_files':len(rows),'completed_files':sum(r['state']=='downloaded' for r in rows),
                'bytes_downloaded':sum(r['bytes_downloaded'] for r in rows),
                'bytes_total':sum(r['bytes_total'] for r in rows) if rows and all(r['bytes_total'] is not None for r in rows) else None,
                'progress_total':sum(r['progress_total'] for r in rows) if rows and all(r['progress_total'] is not None for r in rows) else None,
                'total_is_estimate':any(r['total_is_estimate'] for r in rows),
                'recent_completed':[r for r in rows if r['state']=='downloaded'][-10:],
                'history_completed':completed}
