"""Bounded, verified delivery; leave staged input intact on every failure."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import fcntl
import time
from contextlib import contextmanager

class DeliveryError(RuntimeError):pass

@contextmanager
def release_lock(root, base, folder, month, stopped=lambda:False, waiting=lambda:None, *, blocking=True):
    """Serialize work on one destination month, across independent workers."""
    identity=json.dumps([str(Path(base).resolve()).casefold(),folder.casefold(),month])
    directory=Path(root)/'data/release-locks';directory.mkdir(parents=True,exist_ok=True)
    with (directory/(hashlib.sha256(identity.encode()).hexdigest()+'.lock')).open('a') as lock:
        last=0
        while True:
            if stopped():raise DeliveryError('Stopped while waiting for another operation on this release.')
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if not blocking:raise
                now=time.monotonic()
                if now-last>=1:waiting();last=now
                time.sleep(.2)
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(4*1024*1024):h.update(block)
    return h.hexdigest()

def mount_identity(base):
    base=Path(base).resolve(strict=True)
    # stat triggers automounts; match the actual device, not the autofs wrapper.
    device=base.stat().st_dev
    number=f'{os.major(device)}:{os.minor(device)}'
    mounts=json.loads(subprocess.check_output(['findmnt','-J','-T',str(base),'-o','TARGET,SOURCE,FSTYPE,MAJ:MIN'],text=True,timeout=15))['filesystems']
    matches=[entry for entry in mounts if entry.get('maj:min')==number and entry['fstype']!='autofs']
    if not matches or base.stat().st_dev!=device:raise DeliveryError('Destination mount could not be verified; local files retained.')
    data=max(matches,key=lambda entry:len(Path(entry['target']).parts))
    if str(base).startswith('/mnt/kronos-stl/') or base==Path('/mnt/kronos-stl'):
        if data['fstype']!='cifs' or data['source'].lower()!='//kronos/stl':raise DeliveryError('Kronos is not mounted; staged files were kept locally.')
    return (data['target'],data['source'],data['fstype'],device)


def deliver(source,base,folder,month,filename,progress=lambda done,total:None,phase=lambda state:None,*,subdirectories=(),release_directory=None):
    import re
    if any(not isinstance(v,str) or not v or v in ('.','..') or re.search(r'[\\/<>:"|?*\x00-\x1f]',v) or v.strip()!=v for v in (folder,filename,*subdirectories)):raise DeliveryError('Unsafe destination filename.')
    if release_directory is None and not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',month or ''):raise DeliveryError('Release month needs review.')
    if release_directory is not None:
        if not isinstance(release_directory,str) or not release_directory or release_directory in ('.','..') or re.search(r'[\\/<>:"|?*\x00-\x1f]',release_directory) or release_directory.strip()!=release_directory:raise DeliveryError('Unsafe release folder.')
        month=release_directory
    base=Path(base).resolve(strict=True);identity=mount_identity(base)
    source=Path(source);size=source.stat().st_size
    if shutil.disk_usage(base).free<size+64*1024*1024:raise DeliveryError('Not enough destination space.')
    destination=base.joinpath(folder,month,*subdirectories,filename)
    # Refuse symlinks both before creating and immediately before opening.
    for part in (*destination.relative_to(base).parents,):
        part=base/part
        if part.is_symlink():raise DeliveryError('Destination contains a symbolic link.')
    if destination.is_symlink():raise DeliveryError('Destination contains a symbolic link.')
    # Pin the mounted base before creating any children. Even a disconnect or
    # unmount between these operations cannot redirect writes onto the local SSD.
    basefd=os.open(base,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    dirfd=None
    try:
        if os.fstat(basefd).st_dev!=identity[-1]:raise DeliveryError('Destination mount changed.')
        dirfd=os.dup(basefd)
        for component in (folder,month,*subdirectories):
            try:os.mkdir(component,dir_fd=dirfd)
            except FileExistsError:pass
            childfd=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=dirfd)
            os.close(dirfd);dirfd=childfd
    except BaseException:
        if dirfd is not None:os.close(dirfd)
        raise
    finally:
        os.close(basefd)
    temporary=destination.with_name('.'+filename+'.'+uuid.uuid4().hex+'.partial')
    try:
        if mount_identity(base)!=identity:raise DeliveryError('Destination mount changed.')
        phase('preparing')
        checksum=digest(source)
        if destination.exists():
            if destination.stat().st_size==size and digest(destination)==checksum:return destination,size,checksum
            raise DeliveryError('A different file already exists at the destination; nothing was overwritten.')
        phase('transferring')
        fd=os.open(temporary.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=dirfd)
        with os.fdopen(fd,'wb') as out,source.open('rb') as incoming:
            done=0
            while block:=incoming.read(4*1024*1024):
                out.write(block);done+=len(block);progress(done,size)
            out.flush();os.fsync(out.fileno())
        phase('verifying')
        if digest(temporary)!=checksum:raise DeliveryError('Destination checksum mismatch; local file retained.')
        if mount_identity(base)!=identity:raise DeliveryError('Destination mount changed; local file retained.')
        # Atomic no-overwrite publish on CIFS and local Linux filesystems.
        import ctypes
        libc=ctypes.CDLL(None,use_errno=True)
        result=libc.renameat2(dirfd,os.fsencode(temporary.name),dirfd,os.fsencode(destination.name),1)
        if result:raise OSError(ctypes.get_errno(),'Could not publish verified file without overwriting')
        os.fsync(dirfd)
        return destination,size,checksum
    finally:
        try:os.unlink(temporary.name,dir_fd=dirfd)
        except FileNotFoundError:pass
        os.close(dirfd)
