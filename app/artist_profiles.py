"""Cached artist images/links with private user overrides; no network at runtime."""
import base64
import hashlib
import html
import io
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from PIL import Image, ImageOps, UnidentifiedImageError

def normalized(name):
    return ''.join(c for c in unicodedata.normalize('NFKD',name).casefold() if c.isalnum() and ord(c)<128)

def artist_key(name):
    if not isinstance(name,str) or not name.strip() or len(name)>240 or not normalized(name):
        raise ValueError('Invalid artist name.')
    return hashlib.sha256(normalized(name).encode()).hexdigest()[:24]

def image_bytes(data):
    if len(data)>5_000_000:raise ValueError('Choose an image smaller than 5 MB.')
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width*source.height>20_000_000:raise ValueError('Image dimensions are too large.')
            image=ImageOps.exif_transpose(source).convert('RGBA')
            image.thumbnail((384,384))
            out=io.BytesIO();image.save(out,format='PNG');return out.getvalue()
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as error:
        raise ValueError('Choose a valid PNG, JPEG or WebP image.') from error

class ArtistProfiles:
    def __init__(self,root):
        self.root=Path(root);self.library=self.root/'assets/artist-profiles'
        self._stamp=None;self._defaults={}
        self.path=self.root/'data/artist-profiles.sqlite3'
        self.path.parent.mkdir(exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS overrides (id TEXT PRIMARY KEY, name TEXT NOT NULL, mode TEXT NOT NULL, image BLOB)')
    def connect(self):return sqlite3.connect(self.path,timeout=15)
    def defaults(self):
        try:
            path=self.library/'index.json';stat=path.stat();stamp=(stat.st_ino,stat.st_mtime_ns,stat.st_size)
            if stamp!=self._stamp:
                data=json.loads(path.read_text());self._defaults=data;self._stamp=stamp
            return self._defaults
        except FileNotFoundError:return {}
    def info(self,name):
        key=artist_key(name);default=self.defaults().get(key,{})
        with self.connect() as db:row=db.execute('SELECT mode FROM overrides WHERE id=?',(key,)).fetchone()
        return {'name':name,'key':key,'mode':row[0] if row else 'default',
                'has_default':bool(default.get('image')),'links':default.get('links',{}),
                'link_sources':default.get('link_sources',{}),
                'source':default.get('source'),'checked_at':default.get('checked_at'),
                'status':default.get('status','not_checked')}
    def image(self,name):
        key=artist_key(name)
        with self.connect() as db:row=db.execute('SELECT mode,image FROM overrides WHERE id=?',(key,)).fetchone()
        if row and row[0]=='upload':return bytes(row[1]),'image/png'
        if not row:
            file=self.defaults().get(key,{}).get('image','')
            if re.fullmatch(r'[a-f0-9]{64}\.png',file):
                try:return (self.library/file).read_bytes(),'image/png'
                except FileNotFoundError:pass
        initials=''.join(w[0] for w in name.split()[:2]).upper()
        if len(initials)==1:initials=name[:2].upper()
        hue=int(key[:6],16)%360
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96"><rect width="96" height="96" rx="16" fill="hsl({hue},35%,90%)"/><text x="48" y="59" text-anchor="middle" font-family="sans-serif" font-size="32" fill="hsl({hue},40%,30%)">{html.escape(initials)}</text></svg>'
        return svg.encode(),'image/svg+xml'
    def save(self,payload):
        name=payload.get('name');key=artist_key(name);mode=payload.get('mode')
        if mode not in ('upload','initials','default'):raise ValueError('Invalid logo action.')
        blob=None
        if mode=='upload':
            value=payload.get('image')
            if not isinstance(value,str):raise ValueError('An image is required.')
            try:blob=image_bytes(base64.b64decode(value,validate=True))
            except (ValueError,TypeError) as error:raise ValueError('Invalid image: '+str(error)) from error
        with self.connect() as db:
            if mode=='default':db.execute('DELETE FROM overrides WHERE id=?',(key,))
            else:db.execute('INSERT INTO overrides VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,mode=excluded.mode,image=excluded.image',(key,name,mode,blob))
        return self.info(name)
