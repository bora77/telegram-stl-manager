"""Direct, session-authenticated MyMiniFactory HTTP access. No browser runtime."""
import http.cookiejar
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

BASE = 'https://www.myminifactory.com'

class MMFError(ValueError): pass
class LoginRequired(MMFError): pass

class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__(); self.inside=False; self.buffer=''; self.token=None
    def handle_starttag(self, tag, attrs):
        if tag=='script': self.inside=True; self.buffer=''
    def handle_data(self, text):
        if self.inside: self.buffer+=text
    def handle_endtag(self, tag):
        if tag!='script': return
        self.inside=False
        try: value=json.loads(self.buffer)
        except ValueError: return
        if isinstance(value,dict) and value.get('loginUrl')=='/login_check': self.token=value.get('csrfToken')

class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target=urllib.parse.urlsplit(newurl)
        host=target.hostname or ''
        if target.scheme!='https' or target.username or target.password or target.port not in (None,443) or not (host=='www.myminifactory.com' or re.fullmatch(r'dl\d+\.myminifactory\.com',host)):
            raise MMFError('MMF returned an unexpected download host.')
        if req.data is not None and host!='www.myminifactory.com': raise MMFError('Unexpected login redirect.')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

class MMFClient:
    def __init__(self, session):
        self.path=Path(session); self.cookies=http.cookiejar.MozillaCookieJar()
        if self.path.exists(): self.cookies.load(str(self.path),ignore_discard=True,ignore_expires=False)
        self.http=urllib.request.build_opener(Redirects(),urllib.request.HTTPCookieProcessor(self.cookies))
    def open(self,path,fields=None,headers=None):
        if not path.startswith('/') or path.startswith('//'): raise MMFError('Invalid MMF endpoint.')
        hdr={'User-Agent':'Telegram-STL-Manager/1.0','Accept':'application/json,text/html',**(headers or {})}
        data=None
        if fields is not None:
            hdr.update({'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8','Origin':BASE,'Referer':BASE+'/login'})
            data=urllib.parse.urlencode(fields).encode()
        try: response=self.http.open(urllib.request.Request(BASE+path,data=data,headers=hdr),timeout=30)
        except urllib.error.HTTPError as error:
            if error.code in (401,403): raise LoginRequired('MMF rejected access. Reconnect your account; site verification may be required.') from None
            raise MMFError(f'MMF request failed (HTTP {error.code}). Try again later.') from None
        except (urllib.error.URLError,TimeoutError): raise MMFError('MMF connection failed or timed out. Try again later.') from None
        if response.headers.get('cf-mitigated')=='challenge':
            response.close(); raise LoginRequired('MMF requires additional verification. Reconnect your account.')
        return response
    def metadata(self,path):
        with self.open(path) as response:
            if 'json' not in response.headers.get('Content-Type','') or '/login' in response.url: raise LoginRequired('Your MMF session expired. Reconnect your account.')
            body=response.read(32*1024*1024+1)
            if len(body)>32*1024*1024: raise MMFError('MMF metadata exceeds the supported response size.')
        try: return json.loads(body)
        except ValueError: raise MMFError('MMF returned invalid metadata.') from None
    def save(self):
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(dir=self.path.parent); os.close(fd)
        try:
            self.cookies.save(name,ignore_discard=True,ignore_expires=True)
            os.chmod(name,0o600); os.replace(name,self.path)
        finally:
            if os.path.exists(name):os.unlink(name)
    def login(self,username,password):
        if not isinstance(username,str) or not 0<len(username)<=254 or not isinstance(password,str) or not 0<len(password)<=4096: raise MMFError('Enter your MMF email and MMF password (not your Google password).')
        self.cookies.clear()
        with self.open('/login') as response: body=response.read(2*1024*1024).decode('utf-8','replace')
        form=LoginForm(); form.feed(body)
        if not form.token:raise MMFError('MMF login form has changed or requires verification.')
        with self.open('/login_check',{'_username':username,'_password':password,'_csrf_token':form.token,'_target_path':BASE+'/','_submit':'Login','_remember_me':'on'}) as response:response.read(1024)
        groups=self.groups();self.save();return groups
    def groups(self):
        creators={}
        for endpoint,source in [('userGroups_metadata','USER_GROUP'),('tribes_metadata','TRIBE'),('frontiers_metadata','FRONTIER')]:
            values=self.metadata('/api/data-library/'+endpoint)
            if not isinstance(values,list):raise MMFError('Unexpected MMF creator response.')
            for row in values:
                if source=='TRIBE' and row.get('source','TRIBE')!='TRIBE':continue
                creator=row.get('creator',{}) if source=='FRONTIER' else row
                cid=int(creator['id'])
                creators.setdefault(cid,{'id':cid,'name':creator['name']})
        # Expired memberships can retain owned objects even when the active
        # membership endpoint no longer lists their creator.
        self.library_objects=self.metadata('/api/data-library/objectPreviews')
        if not isinstance(self.library_objects,list):raise MMFError('Unexpected MMF library response.')
        for obj in self.library_objects:
            if obj.get('source') in ('USER_GROUP','TRIBE','FRONTIER') and obj.get('creatorId') and obj.get('creatorName'):
                cid=int(obj['creatorId']);creators.setdefault(cid,{'id':cid,'name':obj['creatorName']})
        return sorted(creators.values(),key=lambda row:row['name'].casefold())
    def downloadables(self,object_id):
        if type(object_id)!=int or object_id<=0:raise MMFError('Invalid MMF object.')
        value=self.metadata(f'/api/data-library/myObjects/object-{object_id}/downloadables')
        if not isinstance(value,dict) or not isinstance(value.get('archives'),list):raise MMFError('Unexpected MMF file response.')
        return value
    def download(self,item,target,progress,stopped):
        target=Path(target); expected=item['size']; offset=target.stat().st_size if target.exists() else 0
        if offset>expected:raise MMFError('Local partial file exceeds the expected size.')
        if offset==expected and expected>0:return
        headers={'Accept':'application/octet-stream','Accept-Encoding':'identity'}
        if offset:headers['Range']=f'bytes={offset}-'
        started=time.monotonic();received=0
        with self.open(f"/download/{item['object_id']}?archive_id={item['archive_id']}",headers=headers) as response:
            kind=response.headers.get('Content-Type','').lower()
            if '/login' in response.url or 'html' in kind or 'json' in kind:raise LoginRequired('MMF returned a login/error page instead of the archive.')
            if response.status==206:
                match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
                if not match or int(match[1])!=offset or int(match[3])!=expected:raise MMFError('MMF returned an inconsistent resume range.')
            elif response.status==200:offset=0
            else:raise MMFError('Unexpected download response.')
            length=response.headers.get('Content-Length')
            if length and int(length)!=expected-offset:raise MMFError('File size changed at MMF; check for updates again.')
            with target.open('ab' if offset else 'wb') as out:
                while True:
                    if stopped():raise MMFError('Stopped; partial download retained for resume.')
                    block=response.read(1024*1024)
                    if not block:break
                    if offset+received+len(block)>expected:raise MMFError('Download exceeds its expected size.')
                    out.write(block);received+=len(block)
                    progress(offset+received,expected,received/max(time.monotonic()-started,.001)/1e6)
                out.flush();os.fsync(out.fileno())
        if target.stat().st_size!=expected:raise MMFError('Incomplete download; partial file retained for resume.')
