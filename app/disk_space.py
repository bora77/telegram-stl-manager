"""Local staging capacity and user-facing disk warnings (no NAS scans)."""
from pathlib import Path
import re
import shutil
import threading
import time
import os
import json

GiB = 1024**3

def space_error(free, needed, reserve=2*GiB, filename='', location='local staging drive'):
    total = needed + reserve
    label = f' for {filename}' if filename else ''
    return (f'Not enough space on the {location}{label}. {free/GiB:.1f} GiB free; '
            f'{total/GiB:.1f} GiB required including {reserve/GiB:.1f} GiB safety reserve. '
            f'Free at least {max(0,total-free)/GiB:.1f} GiB, then press Resume. '
            'Completed files and partial downloads are kept.')

def is_space_error(message):
    return bool(re.search(r'not enough (?:local|staging|destination|space)|no space left|disk (?:is )?full|staging is nearly full',message or '',re.I))

def local_status(path):
    path=Path(path)
    while not path.exists() and path!=path.parent:path=path.parent
    usage=shutil.disk_usage(path)
    threshold=max(10*GiB,min(50*GiB,usage.total*.05))
    return {'free':usage.free,'total':usage.total,'low':usage.free<threshold,
            'threshold':int(threshold),'path':str(path)}

def check_capacity(staging, destination, local_needed, destination_needed, reserve=2*GiB, filename=''):
    """Fresh worker checks; account for simultaneous staging and verified copy."""
    from app.file_delivery import mount_identity
    identity=mount_identity(Path(destination))
    same=Path(staging).stat().st_dev==identity[-1]
    local_required=local_needed+(destination_needed if same else 0)
    free=shutil.disk_usage(staging).free
    if free<local_required+reserve:
        return space_error(free,local_required,reserve,filename,'shared staging/destination drive' if same else 'local staging drive')
    free=shutil.disk_usage(destination).free
    if free<destination_needed+64*1024**2:
        return space_error(free,destination_needed,64*1024**2,filename,'final destination drive')
    return None

_cache={}
_lock=threading.Lock()
def destination_status(path):
    """Never block a queue refresh on a sleeping/disconnected NAS."""
    key=str(path)
    with _lock:
        entry=_cache.setdefault(key,{'checked':0,'busy':False,'value':{'checking':True}})
        if not entry['busy'] and time.monotonic()-entry['checked']>30:
            entry['busy']=True
            def read():
                try:
                    from app.file_delivery import MOUNT_GUARDS
                    guards=json.loads(MOUNT_GUARDS.read_text()) if MOUNT_GUARDS.exists() else []
                    guarded=[Path(g['path']) for g in guards]+[Path('/mnt/telegram-stl-share')]
                    if any(Path(path).is_relative_to(p) and not os.path.ismount(p) for p in guarded):
                        raise OSError('Configured network share is not mounted.')
                    if not Path(path).is_dir():raise OSError('Destination folder is missing.')
                    result=local_status(path)
                except Exception as error:result={'error':'Final destination unavailable: '+str(error)}
                finally:
                    with _lock:
                        entry.update(busy=False,checked=time.monotonic(),value=locals().get('result',{'error':'Could not check final destination space.'}))
            threading.Thread(target=read,name='destination-space',daemon=True).start()
        return dict(entry['value'])
