"""Explicit collage sets: only reviewed, saved images belong to an upload."""
import json
import os
from pathlib import Path
import re
import time
import uuid
from app.file_delivery import digest,mount_identity,release_lock

MANIFEST='.collage-set.json'
FORMAT='telegram-stl-collage-set-v1'

def read(directory):
    path=Path(directory)/MANIFEST
    if not path.exists():return None
    if path.is_symlink() or path.stat().st_size>1024*1024:raise ValueError('Invalid collage set manifest.')
    data=json.loads(path.read_text())
    if not isinstance(data,dict) or data.get('format')!=FORMAT or not isinstance(data.get('files'),list) or not 1<=len(data['files'])<=1000:raise ValueError('Invalid collage set manifest.')
    names=set()
    for entry in data['files']:
        if not isinstance(entry,dict):raise ValueError('Invalid collage file receipt.')
        name=entry.get('filename','')
        if not isinstance(name,str) or Path(name).name!=name or re.search(r'[\\/\x00-\x1f]',name) or not name.lower().endswith('.jpg') or name.casefold() in names:raise ValueError('Invalid or duplicate collage filename.')
        if type(entry.get('size')) is not int or not 0<entry['size']<=32*1024**2 or not re.fullmatch('[a-f0-9]{64}',entry.get('sha256','')):raise ValueError('Invalid collage file receipt.')
        names.add(name.casefold())
    return data

def paths(directory,verify_hash=True):
    directory=Path(directory);data=read(directory)
    if not data:return None
    if any(p.is_symlink() for p in (directory,*directory.parents)):raise ValueError('Collage folders must not be symbolic links.')
    result=[]
    for entry in data['files']:
        path=directory/entry['filename']
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=entry['size'] or (verify_hash and digest(path)!=entry['sha256']):raise ValueError('A selected collage is missing or changed. Save the collage set again: '+path.name)
        result.append(path)
    return result

def finalize(collages,payload):
    release=collages.release(payload.get('release_id'));ids=payload.get('export_ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=1000 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):raise ValueError('Choose distinct completed collage exports.')
    jobs=[collages.export_status(i) for i in ids]
    if any(j['release_id']!=release['id'] or j['state']!='completed' for j in jobs):raise ValueError('Every selected collage must be saved for this release.')
    filenames=[j['filename'].casefold() for j in jobs]
    if len(set(filenames))!=len(filenames):raise ValueError('Give each collage a different output name.')
    folder=Path(release['base'])/release['folder']/release['month']
    with release_lock(collages.root,release['base'],release['folder'],release['month']):
        mount_identity(release['base'])
        old=read(folder)
        with collages.pinned(release) as pinned:
            for j in jobs:collages.verified_data(pinned,release['month']+'/'+j['filename'],j['sha256'],j['bytes'])
            data={'format':FORMAT,'created_at':time.time(),'files':[{'filename':j['filename'],'size':j['bytes'],'sha256':j['sha256'],'export_id':j['id']} for j in jobs]}
            if old:collages.store.atomic_write(collages.cache/('set-backup-'+uuid.uuid4().hex+'.json'),old)
            fd,name=pinned.parent(release['month']+'/'+MANIFEST);temporary='.collage-set-'+uuid.uuid4().hex+'.tmp'
            try:
                out=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                with os.fdopen(out,'w') as stream:json.dump(data,stream);stream.flush();os.fsync(stream.fileno())
                os.replace(temporary,name,src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
            finally:
                try:os.unlink(temporary,dir_fd=fd)
                except FileNotFoundError:pass
                os.close(fd)
    return {'saved':True,'files':data['files']}
