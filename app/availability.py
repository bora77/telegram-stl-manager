"""Browser-triggered metadata checks; never start downloads or register files."""
import json
import threading
import time
from app.release_rules import release_month, in_scope
from app.release_images import is_image_attachment, is_archive, volume_key
from app.telegram_cli import TelegramCLI, safe_filename
from app.organizer_versions import uploads, complete

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
        self.lock=threading.RLock();self.running=False;self.progress={}

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
            try:run=json.loads((self.store.root/'data/run.json').read_text())
            except (OSError,ValueError):run={}
            claimed=checked is not None and run.get('availability_checked_at')==checked
            if claimed:available=[]
            return {'progress':dict(self.progress) if self.running else {},'claimed':claimed,'checking':self.running,'interval_seconds':interval,'checked_at':checked,
                    'next_check_at':checked+interval if checked is not None else 0,'files':len(available),
                    'releases':len({(i['topic_url'],i['month']) for i in available}),
                    'creators':len({i['topic_url'] for i in available}), 'subscribed':len(subs),
                    'errors':saved.get('errors',[]) if same else []}

    def seed_download_run(self, run):
        """Freeze the last availability result into resumable queues, without Telegram I/O."""
        from app.download_plan import DownloadPlan
        from app.topic_catalog import TopicCatalog
        from app.source_scope import load_source
        saved=self.read()
        if not saved.get('checked_at') or saved.get('subscriptions')!=run['subscriptions']:
            return False
        with self.store.history.connect() as db:
            done={(r['topic_url'],r['source_message_id']) for r in db.execute(
                "SELECT topic_url,source_message_id FROM downloads WHERE state='downloaded' OR ignored_at IS NOT NULL")}
        pending=[i for i in saved.get('items',[]) if (i['topic_url'],i['source_message_id']) not in done]
        if not pending:
            raise ValueError('No known downloads remain. Check availability again to find newer files.')
        topics={i['topic_url'] for i in pending}
        run['subscriptions']=[s for s in run['subscriptions'] if s['topic_url'] in topics]
        run['creators_total']=len(run['subscriptions'])
        plan=DownloadPlan(self.store,run);catalog=None
        for sub in run['subscriptions']:
            files=[i for i in pending if i['topic_url']==sub['topic_url']]
            # Older availability snapshots held IDs only. Recover their exact cached files.
            if any('filename' not in i for i in files):
                catalog=catalog or TopicCatalog(self.store.root,load_source(self.store.root))
                cached=catalog.load(sub['topic_url']) or {}
                by_id={i['source_message_id']:i for i in cached.get('files',[])}
                files=[{**by_id.get(i['source_message_id'],{}),**i} for i in files]
            if any('filename' not in i for i in files):
                raise ValueError('Saved download metadata is missing. Check availability again before downloading.')
            entries=[{'item':i,'month':i['month']} for i in files]
            result={'creator':sub['creator'],'latest_release_month':max(i['month'] for i in files),
                    'eligible_files':len(files),'already_downloaded':0,'outside_scope':0,'mode':'availability'}
            plan.save(sub,entries,result,[])
            plan.load(sub) # Validate identities and scope before starting any worker.
        run.update(availability_checked_at=saved['checked_at'],
                   message='Downloading known available files.',
                   scan_warnings=saved.get('errors',[]),warnings=saved.get('errors',[]))
        return True

    def start(self,force=False):
        with self.lock:
            status=self.status()
            if self.running or not status['subscribed'] or (not force and time.time()<status['next_check_at']):return status
            queue=self.store.queue()
            if queue['active'] or queue['organizer_active']:return {**status,'deferred':True}
            self.running=True;self.progress={"done":0,"total":status["subscribed"],"creator":""}
            threading.Thread(target=self.check,daemon=True,name='availability-check').start()
            return self.status()

    def check(self):
        from app.folder_organizer import Organizer
        items=[];errors=[];client=None;subs=[]
        try:
            Organizer(self.store).dismiss_finished()
            subs=self.store.read()['subscriptions']
            client=TelegramCLI(root=self.store.root,config=self.store.config())
            for index,sub in enumerate(subs):
                with self.lock:self.progress={'done':index,'total':len(subs),'creator':sub['creator']}
                try:
                    files=client.list_files(sub['topic_url'],incremental=True)
                    items.extend({**i,'topic_url':sub['topic_url']} for i in eligible(sub,files))
                except Exception:
                    errors.append(sub['creator']+': availability could not be checked.')
                finally:
                    with self.lock:self.progress['done']=index+1
        except Exception:
            errors.append('Telegram availability check failed. Check the connection and CLI login.')
        finally:
            try:
                if client:client.close()
                now=time.time()
                self.store.atomic_write(self.path,{'subscriptions':subs,'items':items,'errors':errors,'checked_at':now,'next_check_at':now+self.store.config()['availability_interval_hours']*3600})
            finally:
                with self.lock:self.running=False
