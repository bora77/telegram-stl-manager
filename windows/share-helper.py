#!/usr/bin/env python3
"""Root-owned, narrowly scoped SMB connector for the dedicated WSL installation."""
import json,os,pwd,re,subprocess,sys,tempfile
from pathlib import Path
DIRECTORY=Path('/etc/telegram-stl');MOUNT=Path('/mnt/telegram-stl-share')
if (DIRECTORY/'mount-path').exists():
    value=(DIRECTORY/'mount-path').read_text().strip()
    if not re.fullmatch(r'/mnt/[A-Za-z0-9][A-Za-z0-9_-]*',value):raise ValueError('Invalid imported mount path.')
    MOUNT=Path(value)
def validate(data):
    server=data.get('server','');share=data.get('share','')
    if not isinstance(server,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',server):raise ValueError('Enter a server hostname or IPv4 address.')
    if not isinstance(share,str) or not re.fullmatch(r'[^/\\,\x00-\x1f]{1,100}',share) or share in ('.','..'):raise ValueError('Enter a share name.')
    for key in ('username','password','domain'):
        value=data.get(key,'')
        if not isinstance(value,str) or len(value)>4096 or any(c in value for c in '\r\n\x00'):raise ValueError('Invalid network credentials.')
    if not data.get('username'):raise ValueError('Enter the share username.')
    return {'server':server,'share':share,**{k:data.get(k,'') for k in ('username','password','domain')}}
def connect(data):
    data=validate(data);account=pwd.getpwnam('stl')
    DIRECTORY.mkdir(mode=0o700,exist_ok=True);MOUNT.mkdir(mode=0o700,exist_ok=True)
    if subprocess.run(['mountpoint','-q',str(MOUNT)]).returncode==0:
        # Never force-unmount a share while files are open.
        subprocess.run(['umount',str(MOUNT)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    fd,name=tempfile.mkstemp(dir=DIRECTORY)
    try:
        with os.fdopen(fd,'w') as f:
            for k in ('username','password','domain'):
                if data[k]:f.write(k+'='+data[k]+'\n')
        options=f'credentials={name},uid={account.pw_uid},gid={account.pw_gid},file_mode=0600,dir_mode=0700,nosuid,nodev,noexec'
        result=subprocess.run(['mount','-t','cifs','//'+data['server']+'/'+data['share'],str(MOUNT),'-o',options],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=45)
        if result.returncode:raise ValueError('Could not connect to the share. Check the server, share name, credentials and Windows network access.')
        target=DIRECTORY/'share.json';fd,tmp=tempfile.mkstemp(dir=DIRECTORY)
        with os.fdopen(fd,'w') as out:json.dump(data,out)
        os.replace(tmp,target)
    finally:Path(name).unlink(missing_ok=True)
    return {'mount':str(MOUNT),'server':data['server'],'share':data['share']}
def main():
    os.umask(0o077)
    if os.geteuid()!=0:raise ValueError('This helper requires the installer-managed permission.')
    raw=sys.stdin.buffer.read(16385)
    if len(raw)>16384:raise ValueError('Request is too large.')
    data=json.loads(raw)
    if data.get('action')=='restore':
        if not (DIRECTORY/'share.json').exists():return {}
        if subprocess.run(['mountpoint','-q',str(MOUNT)]).returncode==0:return {'mount':str(MOUNT)}
        data=json.loads((DIRECTORY/'share.json').read_text())
    elif data.get('action')!='connect':raise ValueError('Unknown share action.')
    return connect(data)
if __name__=='__main__':
    try:print(json.dumps(main()))
    except Exception as e:
        print(json.dumps({'error':str(e) if isinstance(e,ValueError) else 'Network share connection failed.'}));sys.exit(1)
