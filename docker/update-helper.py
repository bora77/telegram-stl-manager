"""Run by Update.command inside the existing container; never controls Docker."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import urllib.request
import urllib.error
import zipfile

ASSET='Telegram-STL-Manager-Docker.zip'
BASE=Path('/state/docker-updates')
LAUNCHERS=tuple(f'{action}-{system}.{suffix}' for system,suffix in (('Linux','sh'),('macOS','command')) for action in ('Install','Start','Stop','Update'))

def version(value):
    if not re.fullmatch(r'\d+\.\d+\.\d+',value):raise ValueError('Invalid version.')
    return tuple(map(int,value.split('.')))

def unpack(archive,destination,expected):
    prefix='Telegram-STL-Manager-Docker/'
    with zipfile.ZipFile(archive) as z:
        seen=set();total=0
        for item in z.infolist():
            name=item.filename
            if not name.startswith(prefix):raise ValueError('Unexpected package layout.')
            rel=name[len(prefix):];parts=PurePosixPath(rel).parts
            if not parts or '..' in parts or '\\' in rel or rel.startswith('/') or any(ord(c)<32 for c in rel):raise ValueError('Unsafe package path.')
            if name in seen or (item.external_attr>>16)&0o170000==0o120000:raise ValueError('Duplicate path or symlink in package.')
            seen.add(name);total+=item.file_size
            if total>2_000_000_000 or len(seen)>50000:raise ValueError('Package exceeds size limits.')
        required=('Start.command','Stop.command','Update.command','compose.yaml','README.md','support/app/VERSION','support/Dockerfile','support/update-helper.py')+LAUNCHERS
        if any(prefix+n not in seen for n in required):raise ValueError('Incomplete Docker update package.')
        if z.read(prefix+'support/app/VERSION').decode().strip()!=expected:raise ValueError('Package version differs from release.')
        for item in z.infolist():
            target=destination/item.filename[len(prefix):]
            if item.is_dir():target.mkdir(parents=True,exist_ok=True);continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with z.open(item) as incoming,target.open('xb') as output:shutil.copyfileobj(incoming,output)
            target.chmod(0o700 if target.suffix in ('.command','.sh') else 0o600)

def prepare(token,offline=None):
    if not re.fullmatch('[a-f0-9]{32}',token):raise ValueError('Invalid transaction ID.')
    work=BASE/token;work.mkdir(parents=True,exist_ok=True,mode=0o700)
    current=Path('/opt/app/VERSION').read_text().strip()
    target=work/'package.zip'
    if offline:
        digest,latest=offline;version(latest)
        if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Invalid checksum.')
    else:
        sys.path.insert(0,'/opt/app')
        from app.updater import Updater
        updater=Updater('/opt/app');repo=updater.settings()['repository']
        with updater.request(f'https://api.github.com/repos/{repo}/releases/latest') as r:data=json.loads(r.read(2_000_001))
        latest=data['tag_name'].removeprefix('v');version(latest)
        if version(latest)<=version(current):print('CURRENT');return
        asset=next((a for a in data.get('assets',[]) if a['name']==ASSET),None)
        if not asset or not re.fullmatch('sha256:[a-f0-9]{64}',asset.get('digest') or ''):raise ValueError('No verified Docker package in this release.')
        digest=asset['digest'][7:];size=asset['size']
        if not isinstance(size,int) or not 0<size<=600_000_000:raise ValueError('Unexpected package size.')
        if not asset['url'].startswith(f'https://api.github.com/repos/{repo}/releases/assets/'):raise ValueError('Unexpected asset URL.')
        print('Downloading Docker update…',file=sys.stderr,flush=True)
        received=0;last=0
        with updater.request(asset['url'],binary=True) as incoming,target.open('xb') as output:
            while block:=incoming.read(1024*1024):
                received+=len(block)
                if received>size:raise ValueError('Download exceeds expected size.')
                output.write(block)
                if received-last>=10*1024**2:print(f'{received//1024**2} / {size//1024**2} MiB',file=sys.stderr,flush=True);last=received
        if received!=size:raise ValueError('Incomplete update download.')
    if version(latest)<=version(current):raise ValueError('Update must be newer than the installed version.')
    with target.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=digest:raise ValueError('Checksum mismatch; existing installation retained.')
    unpack(target,work/'package',latest)
    print(latest)

def guard(action,token):
    payload=json.dumps({'action':action,'token':token}).encode()
    request=urllib.request.Request('http://127.0.0.1:6093/api/updates/docker',data=payload,headers={'Origin':'http://127.0.0.1:6093','Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=15) as response:result=json.load(response)
    except urllib.error.HTTPError as error:
        try:message=json.loads(error.read(4096)).get('error','')
        except ValueError:message=''
        if action=='release' and message=='No matching Docker update reservation.':
            with urllib.request.urlopen('http://127.0.0.1:6093/api/updates',timeout=15) as response:state=json.load(response)
            if not state.get('reserved'):print('READY');return
        raise ValueError(message or 'Update reservation is unavailable. Older packages need one manual Docker update first.') from error
    except Exception as error:raise ValueError('Cannot contact the app for update reservation: '+str(error)) from error
    print('BUSY' if result.get('busy') else 'READY')

if __name__=='__main__':
    try:
        action,token,*args=sys.argv[1:]
        if action=='prepare':prepare(token,args or None)
        elif action in ('reserve','release'):guard(action,token)
        elif action=='cleanup':
            if not re.fullmatch('[a-f0-9]{32}',token):raise ValueError('Invalid transaction ID.')
            shutil.rmtree(BASE/token)
        else:raise ValueError('Unknown update action.')
    except Exception as error:print(str(error),file=sys.stderr);sys.exit(1)
