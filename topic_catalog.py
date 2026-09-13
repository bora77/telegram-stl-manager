"""Completed Telegram metadata scans and their per-topic message cursors."""
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone


class TopicCatalog:
    def __init__(self, root, scope):
        self.path=Path(root)/'data/telegram-catalog.sqlite3';self.scope=scope
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db=self.connect()
        try:
            with db:db.execute('CREATE TABLE IF NOT EXISTS topics (topic TEXT PRIMARY KEY, chat_id TEXT NOT NULL, last_id INTEGER NOT NULL, files TEXT NOT NULL, checked_at TEXT NOT NULL)')
        finally:db.close()

    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        return db

    def validate(self, topic, cursor, files):
        if self.scope.topic_id(topic) is None or type(cursor) is not int or not 0<=cursor<2147483647 or not isinstance(files,list):
            raise ValueError('Invalid saved Telegram scan scope or cursor.')
        seen=set()
        for f in files:
            mid=f.get('source_message_id') if isinstance(f,dict) else None
            if (type(mid) is not int or not 0<mid<=cursor or mid in seen or f.get('topic_url')!=topic
                    or f.get('message_url')!=topic+'/'+str(mid) or not isinstance(f.get('filename'),str)
                    or type(f.get('bytes_total')) is not int or f['bytes_total']<0
                    or type(f.get('dc_id')) is not int or f['dc_id']<=0
                    or type(f.get('document_id')) is not int or f['document_id']==0):
                raise ValueError('Saved Telegram file metadata needs a fresh complete preview.')
            seen.add(mid)
        return files

    def decode(self, row, topic):
        if row is None:return None
        if row['chat_id']!=str(self.scope.chat_id):raise ValueError('Saved Telegram catalog belongs to another source.')
        files=self.validate(topic,row['last_id'],json.loads(row['files']))
        return {'last_id':row['last_id'],'files':files,'checked_at':row['checked_at']}

    def load(self, topic):
        db=self.connect()
        try:return self.decode(db.execute('SELECT * FROM topics WHERE topic=?',(topic,)).fetchone(),topic)
        finally:db.close()

    def commit(self, topic, after, cursor, files):
        self.validate(topic,cursor,files)
        if cursor<after or any(f['source_message_id']<=after for f in files):raise ValueError('Incremental scan crossed its saved boundary.')
        db=self.connect()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                previous=self.decode(db.execute('SELECT * FROM topics WHERE topic=?',(topic,)).fetchone(),topic)
                if after and (not previous or previous['last_id']<after):raise ValueError('Previous Telegram catalog is missing; a complete check is needed.')
                if previous and previous['last_id']>cursor:return previous['files']
                merged=([f for f in previous['files'] if f['source_message_id']<=after] if after else [])+files
                merged.sort(key=lambda f:f['source_message_id'])
                db.execute('INSERT INTO topics VALUES(?,?,?,?,?) ON CONFLICT(topic) DO UPDATE SET chat_id=excluded.chat_id,last_id=excluded.last_id,files=excluded.files,checked_at=excluded.checked_at',
                           (topic,str(self.scope.chat_id),cursor,json.dumps(merged),datetime.now(timezone.utc).isoformat()))
                return merged
        finally:db.close()
