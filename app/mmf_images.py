"""Fallback to the owning MMF product's gallery when archives have no images."""
import hashlib
import io
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from PIL import Image
from app.mmf_client import MMFError


def image_url(url):
    if not isinstance(url,str):return None
    if '/plain/https://' in url:url='https://'+url.split('/plain/https://',1)[1]
    u=urllib.parse.urlsplit(url);host=u.hostname or ''
    if u.scheme!='https' or u.username or u.password or u.port not in (None,443):return None
    if not (host=='assets.myminifactory.com' or re.fullmatch(r'dl\d+\.myminifactory\.com',host)):return None
    if not re.fullmatch(r'/(?:object-assets|object-images)/[^/]+/images/[^/]+',u.path):return None
    path=re.sub(r'/\d+[xX]\d+-','/',u.path)
    return urllib.parse.urlunsplit(('https',host,path,'',''))


class Gallery(HTMLParser):
    def __init__(self,object_id):
        super().__init__();self.object_id=object_id;self.urls=[];self.cover=None;self.script=False;self.buffer=''
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='meta' and a.get('property')=='og:image':self.cover=image_url(a.get('content'))
        if tag=='script':self.script=a.get('type')=='application/ld+json';self.buffer=''
    def handle_data(self,data):
        if self.script:self.buffer+=data
    def handle_endtag(self,tag):
        if tag!='script' or not self.script:return
        self.script=False
        try:value=json.loads(self.buffer)
        except ValueError:return
        def visit(value):
            if isinstance(value,list):
                for v in value:visit(v)
            elif isinstance(value,dict):
                if value.get('@type')=='Product' and str(value.get('sku'))=='3DO'+str(self.object_id):
                    images=value.get('image',[]);images=[images] if isinstance(images,str) else images
                    self.urls.extend(u for v in images if (u:=image_url(v)))
                if '@graph' in value:visit(value['@graph'])
        visit(value)
    def images(self):return list(dict.fromkeys(self.urls or ([self.cover] if self.cover else [])))


class ImageRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not image_url(newurl):raise MMFError('Unexpected MMF image redirect.')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def fetch_images(client,items,work,stopped=lambda:False,progress=lambda *args:None):
    work=Path(work);work.mkdir(parents=True,exist_ok=True);outputs=[];seen=set()
    for oid in sorted({i['object_id'] for i in items}):
        if stopped():raise MMFError('Image collection stopped; retry to continue.')
        progress('fetching_images',0)
        with client.open('/object/3d-print-'+str(oid)) as response:
            body=response.read(4_000_001)
            if len(body)>4_000_000:raise MMFError('MMF product page exceeds image lookup limit.')
        gallery=Gallery(oid);gallery.feed(body.decode('utf-8','replace'))
        for url in gallery.images():
            if url in seen:continue
            seen.add(url)
            if stopped():raise MMFError('Image collection stopped; retry to continue.')
            request=urllib.request.Request(url,headers={'User-Agent':'Telegram-STL-Manager/1.0'})
            with urllib.request.build_opener(ImageRedirects()).open(request,timeout=30) as response:
                data=response.read(25_000_001)
            if len(data)>25_000_000:raise MMFError('MMF image exceeds 25 MB limit.')
            try:
                with Image.open(io.BytesIO(data)) as im:
                    if im.width*im.height>40_000_000:raise ValueError('Image too large')
                    extension={'JPEG':'.jpg','PNG':'.png','WEBP':'.webp'}.get(im.format)
                    if not extension:raise ValueError('Unsupported image format')
                    im.verify()
            except Exception as error:raise MMFError('MMF returned an invalid product image.') from error
            target=work/(str(oid)+'-'+hashlib.sha256(url.encode()).hexdigest()[:16]+extension)
            tmp=target.with_suffix('.tmp');tmp.write_bytes(data);tmp.replace(target);outputs.append(target)
    return outputs
