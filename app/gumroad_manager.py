"""Gumroad subscriptions and durable queue, sharing the release pipeline with MMF."""
import hashlib
import io
import json
import os
import re
from pathlib import Path
import sys
import time
import urllib.request
from PIL import Image
from app.gumroad_client import GumroadClient, Redirects, checked_url
from app.mmf_client import MMFError, LoginRequired
from app.mmf_manager import MMFManager, releases, version_key, month_for


class GumroadManager(MMFManager):
    provider='gumroad'
    id_type=str
    label='Gumroad'

    def progress(self,**values):
        if isinstance(values.get('message'),str):values['message']=values['message'].replace('MMF','Gumroad')
        super().progress(**values)

    def client(self):return GumroadClient(self.session,self.get('purchase_urls',{}))

    def login(self,payload):
        with self.idle():
            # A pending login must not replace a working account before verification.
            client=GumroadClient(self.directory/'pending.cookies')
            result=client.login(payload.get('username'),payload.get('password'),payload.get('token'))
            self.put('verification',{'required':result['verification_required'],'method':result.get('method')})
            if not result['verification_required']:
                client.session.replace(self.session)
                self.put('creators',result['creators']);self.put('auth_required',False)
        return self.state()

    def state(self):
        state=super().state();state['verification']=self.get('verification',{})
        return state

    def refresh_creators(self):
        if not self.session.exists():raise LoginRequired('Connect your Gumroad account in Configuration.')
        creators=self.client().groups();self.put('creators',creators)
        return {'creators':creators}

    def check(self):
        self.progress(phase='checking',message='Loading Gumroad library…',done=0,total=0)
        client=self.client();creators=client.groups()
        subscriptions=self.get('settings',{}).get('subscriptions',[])
        cards=[];urls=self.get('purchase_urls',{})
        for number,sub in enumerate(subscriptions,1):
            self.progress(message='Reading '+sub['name'],done=number-1,total=len(subscriptions))
            for card in client.cards(sub['id'],self.stopped):
                pid=str(card['purchase']['id']);urls[pid]=checked_url(card['purchase']['download_url'])
                cards.append((sub,card))
        client.purchases=urls;items=[]
        for number,(sub,card) in enumerate(cards,1):
            if self.stopped():raise MMFError('Gumroad check stopped. Previous results retained.')
            pid=str(card['purchase']['id']);product=card['product'];name=product['name']
            self.progress(message='Checking '+sub['name']+' · '+name,done=number-1,total=len(cards))
            for remote in client.downloadables(pid)['archives']:
                # Keep named products/folders. Purchase time is not release time.
                folder=remote.get('folder') or ''
                label=folder if month_for('',folder) or re.search(r'\b(welcome|loyalty|rewards?)\b',folder,re.I) else name
                item={'provider':'gumroad','object_id':pid,'archive_id':remote['id'],'size':remote['size'],
                      'filename':remote['name'],'updated_at':remote['updatedAt'],'creator_id':sub['id'],
                      'creator':sub['name'],'folder':sub['folder'],'start_month':sub['start_month'],
                      'object_name':name,'release':label,'release_id':pid+':'+label,'release_created_at':None,
                      'month':month_for('',label),'thumbnail_url':product.get('thumbnail_url')}
                if card.get('is_post'):
                    item['release_created_at']=card.get('published_at');item['release_date_basis']='published_at'
                item['key']=version_key(item);items.append(item)
            if not card.get('is_post'):
                for post in client.post_cards(pid,self.stopped):
                    post_id=str(post['purchase']['id']);urls[post_id]=checked_url(post['purchase']['download_url'])
                    cards.append((sub,post))
            self.progress(done=number,total=len(cards))
        groups=releases(items)
        from app.mmf_release_prepare import downloaded
        historical={i['release_key'] for i in downloaded(self)}
        items=[i for key,group in groups.items() if key in historical or not group[0]['start_month'] or not group[0]['release_month'] or group[0]['release_month']>=group[0]['start_month'] for i in group]
        # Commit one complete snapshot: an offline/interrupted scan keeps the last result.
        with self.db() as db:
            for key,value in [('creators',creators),('purchase_urls',urls),('items',items),('checked_at',time.time()),('auth_required',False)]:
                db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',(key,json.dumps(value)))
        client.save()
        available=sum(i['key'] not in self.completed() for i in items)
        self.progress(phase='complete',message=f'Check complete · {available} new files detected.' if subscriptions else 'Choose artists to check their files.',done=len(cards),total=len(cards))

    def download(self):
        # Existing completed files stay on disk. Only unfinished units (including
        # all parts of an incomplete multipart set) need another transfer.
        from app.mmf_source_layout import source_units
        known=self.completed()-self.pending_redownload_keys()
        queue=self.get('queue',[])
        pending=[i for group in releases(queue).values() for unit in source_units(group)
                 if any(i['key'] not in known for i in unit) for i in unit]
        self.put('queue',pending)
        super().download()

    @staticmethod
    def fetch_images(client,items,work,stopped=lambda:False,progress=lambda *args:None):
        work=Path(work);work.mkdir(parents=True,exist_ok=True);outputs=[]
        urls=list(dict.fromkeys(i.get('thumbnail_url') for i in items if i.get('thumbnail_url')))
        for number,url in enumerate(urls):
            if stopped():raise MMFError('Image collection stopped.')
            progress('fetching_images',number,len(urls))
            with urllib.request.build_opener(Redirects(files=True)).open(checked_url(url,files=True),timeout=30) as response:body=response.read(25_000_001)
            if len(body)>25_000_000:raise MMFError('Gumroad image exceeds 25 MB.')
            with Image.open(io.BytesIO(body)) as image:
                if image.width*image.height>40_000_000:raise MMFError('Gumroad image is too large.')
                ext={'JPEG':'.jpg','PNG':'.png','WEBP':'.webp'}.get(image.format)
                if not ext:raise MMFError('Unsupported Gumroad image format.')
                image.verify()
            target=work/(hashlib.sha256(url.encode()).hexdigest()[:20]+ext);target.write_bytes(body);outputs.append(target)
        return outputs


if __name__=='__main__':
    from app.subscription_store import SubscriptionStore
    os.umask(0o077)
    manager=GumroadManager(SubscriptionStore(Path(sys.argv[1])))
    try:manager.check() if sys.argv[2]=='check' else manager.download()
    except Exception as error:
        if isinstance(error,LoginRequired):manager.put('auth_required',True)
        manager.progress(phase='error',message=str(error) if isinstance(error,(ValueError,OSError)) else 'Gumroad task failed; saved progress retained.',speed_mbps=0)
    finally:os.close(int(sys.argv[3]))
