#!/usr/bin/env python3
"""Maintainer-only collection of public creator profiles. Never runs in the UI."""
import concurrent.futures
from datetime import datetime,timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from app.artist_profiles import artist_key, normalized, image_bytes

PLATFORMS={'patreon.com':'Patreon','myminifactory.com':'MyMiniFactory','cults3d.com':'Cults',
 'only-games.co':'Only-Games','onlygames.co':'Only-Games','thangs.com':'Thangs',
 'cgtrader.com':'CGTrader','printables.com':'Printables','tribes.myminifactory.com':'MyMiniFactory',
 'gumroad.com':'Gumroad','boosty.to':'Boosty','ko-fi.com':'Ko-fi','linktr.ee':'Linktree'}
gate=threading.Lock();next_request=0;blocked=set()

class Page(HTMLParser):
    def __init__(self,text):
        super().__init__();self.meta={};self.links=[];self.avatar=None;self.feed(text)
    def handle_starttag(self,t,attrs):
        a=dict(attrs)
        if t=='meta':self.meta[a.get('property',a.get('name',''))]=a.get('content','')
        if t=='a' and a.get('href'):self.links.append(a['href'])
        if t=='img' and a.get('data-testid')=='ProfileImage':self.avatar=a.get('src')

def platform(url):
    u=urllib.parse.urlsplit(url)
    if u.scheme!='https' or u.username or u.password:return None
    host=(u.hostname or '').lower().removeprefix('www.')
    for domain,label in PLATFORMS.items():
        if host==domain or host.endswith('.'+domain):return label
    return None

def fetch(url,limit=2_000_000):
    global next_request
    host=urllib.parse.urlsplit(url).hostname
    with gate:
        if host in blocked:raise ValueError('Host temporarily blocked; lookup deferred')
        now=time.monotonic();delay=max(0,next_request-now);next_request=max(now,next_request)+.4
    if delay:time.sleep(delay)
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=12) as r:
            data=r.read(limit+1)
            if len(data)>limit:raise ValueError('Response exceeds size limit')
            return data
    except urllib.error.HTTPError as e:
        if e.code in (403,406,429):
            with gate:blocked.add(host)
        raise

def linked_profiles(page,name):
    result={}
    for url in page.links:
        label=platform(url)
        if not label:continue
        u=urllib.parse.urlsplit(url)
        # A cross-link must still name the creator in a complete path segment
        # or subdomain. Never collect product, login or generic platform links.
        parts=[p for p in u.path.split('/') if p]
        match=lambda p:normalized(urllib.parse.unquote(p))==normalized(name)
        path=None
        if label=='Patreon':
            if parts and parts[0] in ('c','cw'):parts=parts[1:]
            if parts and match(parts[0]):path='/'+parts[0]
        elif label=='MyMiniFactory' and len(parts)>=2 and parts[0]=='users' and match(parts[1]):path='/users/'+parts[1]
        elif label=='Cults' and len(parts)>=3 and parts[1]=='users' and match(parts[2]):path='/en/users/'+parts[2]+'/3d-models'
        elif label=='Only-Games' and len(parts)==2 and parts[0]=='collections' and match(parts[1]):path=u.path
        elif label=='Thangs' and len(parts)>=2 and parts[0]=='designer' and match(parts[1]):path='/designer/'+parts[1]
        elif label=='CGTrader' and len(parts)==1 and match(parts[0]):path=u.path
        elif label in ('Boosty','Ko-fi','Linktree') and len(parts)==1 and match(parts[0]):path=u.path
        elif label=='Gumroad' and match((u.hostname or '').split('.')[0]) and not parts:path='/'
        elif label=='Printables' and len(parts)>=2 and parts[0]=='@'+name:path='/'+parts[0]
        if path is not None:result[label]=urllib.parse.urlunsplit((u.scheme,u.netloc,path,'',''))
    return result

def collect(name,sources=None):
    key=artist_key(name);record={'name':name,'links':{},'link_sources':{},'status':'not_found',
        'checked_at':datetime.now(timezone.utc).isoformat(),'attempts':[]}
    handle=normalized(name)
    if sources is None:
        sources=[('Patreon','https://www.patreon.com/'+handle),('Linktree','https://linktr.ee/'+handle)]
    image=None
    for label,url in sources:
        try:
            text=fetch(url).decode('utf-8');page=Page(text)
            title=page.meta.get('og:title','')
            if label=='Patreon':
                display=re.sub(r'^Get more from (.*?) on Patreon$',r'\1',title)
                display=re.split(r'\s+[—–|]\s+|\s+is creating\s+',display)[0]
                trim=lambda value:re.sub(r'\s+(?:miniatures|studio|studios|3d)$','',value,flags=re.I)
                if normalized(display)!=normalized(name) and normalized(trim(display))!=normalized(trim(name)):
                    raise ValueError('Creator name does not match')
                context=page.meta.get('og:description','')+' '+title
                if not re.search(r'3d|stl|miniature|sculpt|tabletop|terrain|printable',context,re.I):raise ValueError('Creator context is unclear')
                clean=text.replace('\\"','"').replace('\\u0026','&')
                m=re.search(r'"avatar_photo_image_urls"\s*:\s*(\{[^{}]+\})',clean)
                avatar=json.loads(m[1]).get('default') if m else None
            else:
                links=linked_profiles(page,name)
                if not any(k in links for k in ('Patreon','MyMiniFactory','Cults','Thangs','Only-Games')):
                    raise ValueError('No matching creator storefront cross-link')
                avatar=page.avatar
            record['links'][label]=url
            found=linked_profiles(page,name)
            record['links'].update(found)
            record['link_sources'].update({k:url for k in {label,*found}})
            record['status']='links_found'
            if avatar and not image:
                u=urllib.parse.urlsplit(avatar);host=u.hostname or ''
                if u.scheme!='https' or not (host.endswith('.patreonusercontent.com') or host=='ugc.production.linktr.ee'):
                    raise ValueError('Unrecognized avatar host')
                try:
                    image=image_bytes(fetch(avatar,5_000_000));record['source']=url
                except Exception as e:record['attempts'].append(label+' image: '+str(e))
        except Exception as e:record['attempts'].append(label+': '+str(e))
    if image:
        filename=hashlib.sha256(image).hexdigest()+'.png'
        (ROOT/'assets/artist-profiles'/filename).write_bytes(image)
        record['image']=filename;record['status']='image_found'
    return key,record

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--limit',type=int);p.add_argument('--retry',action='store_true');p.add_argument('--retry-missing',action='store_true');args=p.parse_args()
    folder=ROOT/'assets/artist-profiles';folder.mkdir(parents=True,exist_ok=True)
    index=folder/'index.json'
    data=json.loads(index.read_text()) if index.exists() else {}
    creators=json.loads((ROOT/'data/creators.json').read_text())['creators']
    names=list(dict.fromkeys(c['name'] for c in creators))
    todo=[n for n in names if args.retry or artist_key(n) not in data or args.retry_missing and not data.get(artist_key(n),{}).get('image')]
    if args.limit:todo=todo[:args.limit]
    print(f'Catalog: {len(names)} artists; collecting {len(todo)}',flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for i,(key,record) in enumerate(pool.map(collect,todo),1):
            previous=data.get(key,{})
            # A blocked page or temporary lookup failure must not discard a
            # previously collected image, reviewed alias, or known storefront.
            record['links']={**previous.get('links',{}),**record.get('links',{})}
            record['link_sources']={**previous.get('link_sources',{}),**record.get('link_sources',{})}
            if previous.get('image') and not record.get('image'):
                record['image']=previous['image'];record['source']=previous.get('source');record['status']='image_found'
            elif record['links'] and record['status']=='not_found':record['status']='links_found'
            data[key]=record
            tmp=index.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');os.replace(tmp,index)
            if i%25==0 or i==len(todo):
                print(f'{i}/{len(todo)} checked; {sum(bool(v.get("image")) for v in data.values())} logos; {sum(bool(v.get("links")) for v in data.values())} profiles with links',flush=True)
    print('Finished. Unmatched artists retain initials.',flush=True)

if __name__=='__main__':main()
