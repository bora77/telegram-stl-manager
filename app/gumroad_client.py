"""Purchased-library access through Gumroad's authenticated website JSON.

Schemas: antiwork/gumroad LibraryPresenter and UrlRedirectPresenter. No seller
OAuth token, mobile application secret, scraping service or browser runtime.
"""
import hashlib
import http.cookiejar
import http.client
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from app.mmf_client import MMFError, LoginRequired

BASE = 'https://gumroad.com'


def checked_url(value, *, files=False):
    url = urllib.parse.urlsplit(urllib.parse.urljoin(BASE, value))
    host = (url.hostname or '').lower()
    allowed = host == 'gumroad.com' or host.endswith('.gumroad.com')
    if files:
        allowed = allowed or host.endswith('.gumroad.io') or host.endswith('.amazonaws.com') or host.endswith('.cloudfront.net')
    if url.scheme != 'https' or url.username or url.password or url.port not in (None, 443) or not allowed:
        raise MMFError('Gumroad returned an unsupported download host.')
    return urllib.parse.urlunsplit((url.scheme,url.netloc,urllib.parse.quote(url.path,safe="/%:@!$&'()*+,;=-._~"),url.query,url.fragment))


class Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, files=False):self.files=files
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, checked_url(newurl,files=self.files))


class GumroadClient:
    def __init__(self, session, purchases=None):
        self.session=Path(session);self.purchases=purchases or {}
        self.cookies=http.cookiejar.MozillaCookieJar(str(self.session))
        if self.session.exists():self.cookies.load(ignore_discard=True,ignore_expires=False)
        self.opener=urllib.request.build_opener(Redirects(),urllib.request.HTTPCookieProcessor(self.cookies))
        self.cache={}

    def save(self):
        self.session.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        temporary=self.session.with_suffix('.tmp')
        self.cookies.save(str(temporary),ignore_discard=True,ignore_expires=False)
        os.chmod(temporary,0o600);temporary.replace(self.session)

    def login(self, username=None, password=None, token=None):
        if token:
            page=self.metadata('/two-factor',raw=True)
            if page.get('component')!='TwoFactorAuthentication/Show':raise LoginRequired('The verification session expired. Sign in again.')
            props=page['props']
            page=self.metadata('/two-factor',raw=True,payload={'user_id':props['user_id'],'token':token,'authenticity_token':props['authenticity_token'],'next':'/library'})
        else:
            if not isinstance(username,str) or not username.strip() or not isinstance(password,str) or not password:
                raise MMFError('Enter your Gumroad email and password.')
            page=self.metadata('/login?next=/library',raw=True)
            if page.get('component')!='Logins/New':raise MMFError('Unexpected Gumroad sign-in response.')
            csrf=page.get('props',{}).get('authenticity_token')
            if not csrf:raise MMFError('Gumroad did not provide a sign-in token.')
            page=self.metadata('/login',raw=True,payload={'user':{'login_identifier':username.strip(),'password':password},'next':'/library','authenticity_token':csrf})
        if page.get('component')=='TwoFactorAuthentication/Show':
            self.save()
            return {'verification_required':True,'method':page['props'].get('two_factor_method','email')}
        if page.get('component')=='Logins/New':raise LoginRequired('Gumroad sign-in failed. Check your email and password.')
        creators=self.groups();self.save()
        return {'verification_required':False,'creators':creators}

    def metadata(self, path, component=None, *, raw=False, payload=None):
        headers={'User-Agent':'Telegram-STL-Manager','Accept':'application/json','X-Requested-With':'XMLHttpRequest','X-Inertia':'true'}
        body=None
        if payload is not None:
            body=json.dumps(payload).encode();headers['Content-Type']='application/json';headers['Origin']=BASE;headers['Referer']=BASE+'/login'
        try:
            with self.opener.open(urllib.request.Request(checked_url(path),headers=headers,data=body),timeout=40) as response:
                body=response.read(16_000_001)
                if len(body)>16_000_000:raise MMFError('Gumroad metadata exceeds the response limit.')
                if not raw and '/login' in urllib.parse.urlsplit(response.url).path:raise LoginRequired('Gumroad session expired. Reconnect in Configuration.')
            data=json.loads(body)
        except urllib.error.HTTPError as error:
            if error.code in (401,403):raise LoginRequired('Gumroad requires sign-in or verification. Reconnect in Configuration.') from None
            raise MMFError(f'Gumroad request failed (HTTP {error.code}). Retry later.') from None
        except (urllib.error.URLError,TimeoutError):raise MMFError('Could not reach Gumroad. Check your internet connection and retry.') from None
        except (ValueError,UnicodeError) as error:
            if isinstance(error,MMFError):raise
            raise LoginRequired('Gumroad did not return account JSON. Try signing in again in Configuration.') from None
        if component:
            if not isinstance(data,dict) or data.get('component')!=component:
                raise LoginRequired('Gumroad requires account verification, or this purchase is no longer accessible.')
            data=data.get('props')
        if not isinstance(data,dict):raise MMFError('Unexpected Gumroad response. No availability results were replaced.')
        return data

    def library(self, creator=None, page=1):
        query={'page':page,'sort':'recently_updated'}
        if creator is not None:query['creators']=creator
        data=self.metadata('/library?'+urllib.parse.urlencode(query),'Library/Index')
        if not isinstance(data.get('results'),list) or not isinstance(data.get('pagination'),dict):raise MMFError('Gumroad library format changed.')
        return data

    def groups(self):
        data=self.library();creators=data.get('creators')
        if not isinstance(creators,list):raise MMFError('Gumroad did not return library creators.')
        return [{'id':str(c['id']),'name':c['name']} for c in creators]

    def cards(self, creator, stopped=lambda:False):
        page=1;seen=set()
        while True:
            if stopped():raise MMFError('Gumroad check stopped. Previous results retained.')
            data=self.library(creator,page)
            for card in data['results']:
                pid=str(card['purchase']['id'])
                if pid in seen:continue
                seen.add(pid);yield card
            pages=int(data['pagination']['pages'])
            if page>=pages:break
            if page>=10000:raise MMFError('Gumroad pagination limit exceeded.')
            page+=1

    def content(self, purchase_id):
        pid=str(purchase_id)
        if pid not in self.cache:
            url=self.purchases.get(pid)
            if not url:raise MMFError('This purchase needs a fresh Gumroad availability check.')
            data=self.metadata(url,'UrlRedirects/DownloadPage')
            if not isinstance(data.get('content'),dict) or not isinstance(data['content'].get('content_items'),list):raise MMFError('Gumroad purchase content is unavailable.')
            self.cache[pid]=data
        return self.cache[pid]

    def post_cards(self,purchase_id,stopped=lambda:False):
        """Membership updates can deliver their own file collection."""
        posts=self.content(purchase_id)['content'].get('posts') or []
        if not isinstance(posts,list):raise MMFError('Gumroad membership updates have an unexpected format.')
        for post in posts:
            if stopped():raise MMFError('Gumroad check stopped. Previous results retained.')
            if not post.get('view_url'):continue
            data=self.metadata(post['view_url'],'Posts/Show')
            if data.get('download_url'):
                yield {'purchase':{'id':str(purchase_id)+':post:'+str(post['id']),'download_url':data['download_url']},
                       'product':{'name':data.get('subject') or post['name']},
                       'published_at':data.get('published_at'),'is_post':True}

    def downloadables(self,purchase_id):
        data=self.content(purchase_id);archives=[]
        def visit(entries, folder=''):
            for entry in entries:
                if entry.get('type')=='folder':visit(entry.get('children',[]),entry.get('name') or folder)
                elif entry.get('type')=='file' and entry.get('download_url'):
                    if entry.get('processing'):raise MMFError('Gumroad is still preparing a file. Check again later.')
                    name=str(entry['file_name']);ext=str(entry.get('extension') or '').lower()
                    if ext and not name.lower().endswith('.'+ext):name+='.'+ext
                    size=entry.get('file_size')
                    if not isinstance(size,(int,float)) or size<0:raise MMFError('Gumroad returned an unknown file size.')
                    archives.append({'id':str(entry['id']),'name':name,'size':int(size),'updatedAt':'','folder':folder,'url':checked_url(entry['download_url'])})
        visit(data['content']['content_items'])
        return {'archives':archives}

    def download(self,item,path,progress,stopped):
        file=next((a for a in self.downloadables(item['object_id'])['archives'] if a['id']==item['archive_id']),None)
        if not file:raise MMFError('Gumroad file is no longer available. Check again.')
        response=self.metadata(file['url'])
        files=response.get('files',[])
        if len(files)!=1 or not files[0].get('url'):raise MMFError('Gumroad did not provide a downloadable file.')
        url=checked_url(files[0]['url'],files=True)
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        if path.is_file() and path.stat().st_size==item['size']:return
        partial=path.with_name(path.name+'.partial');offset=partial.stat().st_size if partial.exists() else 0
        if partial.exists() and offset==item['size']:
            partial.replace(path);return
        if offset>item['size']:offset=0
        headers={'User-Agent':'Telegram-STL-Manager','Accept-Encoding':'identity'}
        if offset:headers['Range']=f'bytes={offset}-'
        started=time.monotonic();received=offset
        try:
            # Signed storage requests never carry the Gumroad session cookie.
            with urllib.request.build_opener(Redirects(files=True)).open(urllib.request.Request(url,headers=headers),timeout=40) as remote:
                if remote.status==206:
                    value=remote.headers.get('Content-Range','')
                    if not value.startswith(f'bytes {offset}-') or not value.endswith('/'+str(item['size'])):raise MMFError('Gumroad returned an invalid resume range.')
                else:offset=received=0
                if 'text/html' in remote.headers.get('Content-Type',''):raise MMFError('Gumroad returned a web page instead of a file.')
                with partial.open('ab' if offset else 'wb') as output:
                    while True:
                        if stopped():raise MMFError('Download stopped. Resume to continue.')
                        chunk=remote.read(256*1024)
                        if not chunk:break
                        output.write(chunk);received+=len(chunk)
                        if received>item['size']:raise MMFError('Gumroad file changed size. Check again.')
                        progress(received,item['size'],(received-offset)/max(.001,time.monotonic()-started)/1e6)
                    output.flush();os.fsync(output.fileno())
        except (urllib.error.URLError,TimeoutError,http.client.IncompleteRead,ConnectionError):raise MMFError('Gumroad transfer interrupted. Resume to retry.') from None
        if received!=item['size']:raise MMFError('Gumroad transfer incomplete. Resume to retry.')
        partial.replace(path)
