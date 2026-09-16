"""Browser-triggered metadata checks; never start downloads or register files."""
import json
import threading
import time
from release_rules import release_month, in_scope
from release_images import is_image_attachment, is_archive, volume_key
from telegram_cli import TelegramCLI, safe_filename
from organizer_versions import uploads, complete

INTERVAL = 4 * 60 * 60


def eligible(sub, files):
    entries=[]
    for item in files:
        month=release_month(item['filename'])
        if not is_image_attachment(item) and safe_filename(item['filename']) and month and in_scope(sub,month):
            entries.append({**item,'month':month})
    groups={};excluded=set()
    for item in entries:
        if is_archive(item['filename']):groups.setdefault((item['month'],volume_key(item['filename'])[0]),[]).append(item)
    for items in groups.values():
        variants=[v for v in uploads(items) if complete(v)]
        if not variants:continue
        best=max(variants,key=lambda v:(sum(i['bytes_total'] for i in v),max(i['source_message_id'] for i in v)))
        chosen={i['source_message_id'] for i in best}
        excluded.update(i['source_message_id'] for i in items if i['source_message_id'] not in chosen)
    return [i for i in entries if i['source_message_id'] not in excluded]


class Availability:
    def __init__(self,store):
        self.store=store;self.path=store.root/'data/availability.json'
        self.lock=threading.RLock();self.running=False

    def read(self):
        try:return json.loads(self.path.read_text())
        except (OSError,ValueError):return {}

    def status(self):
        with self.lock:
            saved=self.read();subs=self.store.read()['subscriptions']
            same=saved.get('subscriptions')==subs
            interval=self.store.config()['availability_interval_hours']*3600
            checked=saved.get('checked_at') if same else None
            with self.store.history.connect() as db:
                done={(r['topic_url'],r['source_message_id']) for r in db.execute("SELECT topic_url,source_message_id FROM downloads WHERE state='downloaded' OR ignored_at IS NOT NULL")}
            available=[i for i in saved.get('items',[]) if (i['topic_url'],i['source_message_id']) not in done] if same else []
            return {'checking':self.running,'interval_seconds':interval,'checked_at':checked,
                    'next_check_at':checked+interval if checked is not None else 0,'files':len(available),
                    'releases':len({(i['topic_url'],i['month']) for i in available}),
                    'creators':len({i['topic_url'] for i in available}), 'subscribed':len(subs),
                    'errors':saved.get('errors',[]) if same else []}

    def start(self):
        with self.lock:
            status=self.status()
            if self.running or not status['subscribed'] or time.time()<status['next_check_at']:return status
            queue=self.store.queue()
            if queue['active'] or queue['organizer_active']:return {**status,'deferred':True}
            self.running=True
            threading.Thread(target=self.check,daemon=True,name='availability-check').start()
            return self.status()

    def check(self):
        items=[];errors=[];client=None;subs=[]
        try:
            subs=self.store.read()['subscriptions']
            client=TelegramCLI(root=self.store.root,config=self.store.config())
            for sub in subs:
                try:
                    files=client.list_files(sub['topic_url'],incremental=True)
                    items.extend({'topic_url':sub['topic_url'],'source_message_id':i['source_message_id'],'month':i['month']} for i in eligible(sub,files))
                except Exception:
                    errors.append(sub['creator']+': availability could not be checked.')
        except Exception:
            errors.append('Telegram availability check failed. Check the connection and CLI login.')
        finally:
            try:
                if client:client.close()
                now=time.time()
                self.store.atomic_write(self.path,{'subscriptions':subs,'items':items,'errors':errors,'checked_at':now,'next_check_at':now+self.store.config()['availability_interval_hours']*3600})
            finally:
                with self.lock:self.running=False
