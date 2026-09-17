#!/usr/bin/env python3
"""Preserve finished downloads stored inside the app's WSL disk before removal."""
import hashlib,json,os,shutil,subprocess,sys
from pathlib import Path

def destination(root, external_mount=None):
    settings=root/'data/settings.json'
    if not settings.exists():return None
    value=json.loads(settings.read_text()).get('download_directory')
    if not value or not Path(value).is_absolute():raise ValueError('Cannot verify the configured download destination.')
    source=Path(value)
    # A configured managed SMB destination stays external even while unmounted.
    if external_mount is not None and source.is_relative_to(external_mount):return None
    if not source.exists():return None
    source=source.resolve(strict=True)
    # Windows drives and network mounts live outside the WSL disk being removed.
    filesystem=subprocess.check_output(['findmnt','-n','-T',str(source),'-o','FSTYPE'],text=True,timeout=10).split()
    if any(f in ('cifs','smb3','9p','drvfs','nfs','nfs4') for f in filesystem):return None
    return source

def manifest(source):
    result=[]
    for parent,dirs,files in os.walk(source,followlinks=False):
        for name in dirs+files:
            path=Path(parent)/name
            if path.is_symlink():raise ValueError('Local downloads contain links. Move these downloads to an external destination before uninstalling.')
        for name in files:
            path=Path(parent)/name
            if not path.is_file():raise ValueError('Unexpected entry in local downloads; nothing was removed.')
            result.append((path.relative_to(source).as_posix(),path.stat().st_size))
    return sorted(result)

def checksum(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while block:=f.read(4*1024*1024):h.update(block)
    return h.hexdigest()

def preserve(source,target):
    before=manifest(source)
    if target.exists() and any(target.iterdir()):raise ValueError('Backup directory is not empty.')
    target.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(target).free<sum(size for _,size in before)+64*1024*1024:raise ValueError('Not enough space to preserve local downloads. Uninstall cancelled.')
    for name,size in before:
        incoming=source/name;out=target/name;out.parent.mkdir(parents=True,exist_ok=True)
        with incoming.open('rb') as f,out.open('xb') as g:
            shutil.copyfileobj(f,g,4*1024*1024);g.flush();os.fsync(g.fileno())
        if out.stat().st_size!=size or checksum(incoming)!=checksum(out):raise ValueError('Local download backup verification failed. Nothing was uninstalled.')
    if manifest(source)!=before:raise ValueError('Local downloads changed during backup. Nothing was uninstalled.')
    return {'preserved_files':len(before),'backup':str(target)}

def main():
    root=Path('/home/stl/telegram-stl') if Path('/etc/telegram-stl-managed').exists() else Path(__file__).resolve().parent.parent
    external_mount=None
    managed=Path('/etc/telegram-stl')
    if Path('/etc/telegram-stl-managed').exists() and (managed/'share.json').exists():
        mount_file=managed/'mount-path'
        value=mount_file.read_text().strip() if mount_file.exists() else '/mnt/telegram-stl-share'
        import re
        if not re.fullmatch(r'/mnt/[A-Za-z0-9][A-Za-z0-9_-]*',value):raise ValueError('Invalid managed share mount; nothing was removed.')
        external_mount=Path(value)
    source=destination(root,external_mount)
    if not source:return {'local_files':0}
    files=manifest(source)
    if len(sys.argv)==1:return {'local_files':len(files),'bytes':sum(size for _,size in files)}
    if len(sys.argv)!=2:raise ValueError('Unexpected arguments.')
    return preserve(source,Path(sys.argv[1]))
if __name__=='__main__':
    try:print(json.dumps(main()))
    except Exception as error:
        print(json.dumps({'error':str(error)}));sys.exit(1)
