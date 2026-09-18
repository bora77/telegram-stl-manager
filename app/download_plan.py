"""Atomic per-creator checkpoints for a manually resumable download run."""
import hashlib
import json
import re

from app.release_rules import release_month, in_scope
from app.source_scope import load_source
from app.telegram_cli import safe_filename


class DownloadPlan:
    def __init__(self, store, run):
        self.store=store;self.run=run;self.source=load_source(store.root)
        if not isinstance(run.get('id'),str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',run['id']):raise ValueError('Invalid saved download run.')
        if any(self.source.topic_id(s.get('topic_url')) is None for s in run['subscriptions']):raise ValueError('Saved run belongs to another Telegram source.')
        identity=[self.source.chat_id,self.source.toc_message_id,run['subscriptions'],run['download_directory']]
        self.signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        self.directory=store.root/'data/download-plans'/run['id']
        if self.directory.is_symlink():raise ValueError('Invalid saved queue directory.')

    def path(self, sub):
        return self.directory/(str(self.source.topic_id(sub['topic_url']))+'.json')

    def load(self, sub):
        path=self.path(sub)
        if not path.exists():return None
        if path.is_symlink():raise ValueError('Invalid saved queue checkpoint.')
        record=json.loads(path.read_text())
        if (not isinstance(record,dict) or record.get('signature')!=self.signature or record.get('topic_url')!=sub['topic_url']
                or not isinstance(record.get('items'),list) or not isinstance(record.get('result'),dict)
                or record['result'].get('creator')!=sub['creator'] or not isinstance(record.get('warnings'),list)
                or any(not isinstance(w,str) for w in record['warnings'])):
            raise ValueError('The saved download queue no longer matches this run. Start a new run to check it again.')
        seen=set()
        for entry in record['items']:
            if not isinstance(entry,dict) or not isinstance(entry.get('item'),dict):raise ValueError('Invalid saved attachment.')
            item=entry['item'];mid=item.get('source_message_id');month=entry.get('month')
            if (type(mid) is not int or mid<=0 or mid in seen or not safe_filename(item.get('filename',''))
                    or type(item.get('bytes_total')) is not int or item['bytes_total']<0 or item.get('topic_url')!=sub['topic_url']
                    or item.get('message_url')!=sub['topic_url']+'/'+str(mid) or not isinstance(month,str)
                    or month!=release_month(item['filename']) or not in_scope(sub,month)):
                raise ValueError('Saved attachment identity or scope changed; start a new run.')
            seen.add(mid)
        return record

    def save(self, sub, items, result, warnings):
        self.store.atomic_write(self.path(sub),{'signature':self.signature,'topic_url':sub['topic_url'],
                                             'items':items,'result':result,'warnings':warnings})

    def seed_legacy(self):
        """Recover old checked queues from exact history IDs and cached metadata.

        An interrupted scan's partial result is excluded by creators_checked.
        Newer catalog posts are never added to an already checked old run.
        """
        if self.run.get('plan_version')==1:return
        from app.topic_catalog import TopicCatalog
        catalog=TopicCatalog(self.store.root,self.source)
        results=self.run.get('creator_results',[])
        checked=min(self.run.get('creators_checked',0),len(results),len(self.run['subscriptions']))
        for sub,result in zip(self.run['subscriptions'][:checked],results[:checked]):
            if result.get('creator')!=sub['creator']:break
            if self.path(sub).exists():continue
            with self.store.history.connect() as db:
                rows=[dict(r) for r in db.execute('SELECT source_message_id,filename,bytes_total,release_month FROM downloads WHERE batch_id=? AND topic_url=?',
                                                 (self.run['id'],sub['topic_url']))]
            items=[]
            if rows:
                saved=catalog.load(sub['topic_url'])
                if saved is None:continue
                by_id={f['source_message_id']:f for f in saved['files']}
                for row in rows:
                    item=by_id.get(row['source_message_id'])
                    if not item or item['filename']!=row['filename'] or item['bytes_total']!=row['bytes_total']:break
                    month=release_month(item['filename'])
                    if month!=row['release_month'] or not in_scope(sub,month):break
                    items.append({'item':item,'month':month})
                else:
                    self.save(sub,items,result,[])
                continue
            self.save(sub,items,result,[])
