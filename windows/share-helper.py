#!/usr/bin/env python3
"""Root-owned, narrowly scoped SMB connector for the dedicated WSL installation."""
import ipaddress,json,os,pwd,re,subprocess,sys,tempfile
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
class ResolutionRequired(ValueError):
    pass

def resolve_server(server):
    # Validate before passing the hostname into a fixed PowerShell expression.
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',server):raise ValueError('Enter a server hostname or IPv4 address.')
    try:return str(ipaddress.IPv4Address(server))
    except ValueError:pass
    commands=[(['getent','ahostsv4',server],5),
              (['/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-Command',
                "$ErrorActionPreference='Stop'; [System.Net.Dns]::GetHostAddresses('"+server+"') | Where-Object {$_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork} | ForEach-Object {$_.IPAddressToString}"],12)]
    for command,timeout in commands:
        try:result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=timeout)
        except (OSError,subprocess.TimeoutExpired):continue
        if result.returncode:continue
        for line in result.stdout.splitlines():
            parts=line.strip().split()
            if not parts:continue
            try:return str(ipaddress.IPv4Address(parts[0]))
            except ValueError:continue
    raise ResolutionRequired('The NAS name could not be resolved automatically. Enter its LAN IPv4 address below, then connect again.')

def mount_failure(stderr):
    """Return only classified diagnostics, never raw mount output or credentials."""
    text=stderr.decode('utf-8','replace') if isinstance(stderr,bytes) else str(stderr or '')
    lowered=text.lower()
    if any(term in lowered for term in ('could not resolve','unable to resolve','name or service not known','temporary failure in name resolution')):
        return 'NAS hostname could not be resolved from the app. Replace the server name in the network path with the NAS LAN IP address and retry.'
    match=re.search(r'mount error\s*\((\d{1,4})\)',text,re.I)
    code=int(match.group(1)) if match else None
    messages={
        13:'SMB access denied. Check the NAS username, password, domain (if used), and permission to access this shared folder.',
        2:'SMB share or mount path was not found. Check the shared-folder name immediately after the NAS address.',
        101:'The NAS network is unreachable from the app. Check the LAN connection, VPN and firewall.',
        113:'The NAS could not be reached from the app. Check its LAN IP address, VPN and firewall.',
        110:'The SMB connection timed out. Check the NAS address and that SMB port 445 is reachable from the app.',
        111:'The NAS refused the SMB connection. Check that SMB file sharing is enabled and port 445 is allowed.',
        95:'The SMB operation is not supported. Check SMB2/SMB3 compatibility on the NAS; do not enable SMB1.',
        112:'The NAS host is down or unavailable. Check its address and network connection.',
    }
    if code in messages:return messages[code]+f' (mount error {code})'
    if code is not None:return f'SMB mount failed (mount error {code}). Check the NAS SMB service and its connection logs.'
    return 'SMB mount failed without a recognized error code. Check the NAS address, shared-folder name, SMB service and NAS connection logs.'

def connect(data):
    data=validate(data);address=resolve_server(data['server']);account=pwd.getpwnam('stl')
    DIRECTORY.mkdir(mode=0o700,exist_ok=True);MOUNT.mkdir(mode=0o700,exist_ok=True)
    if subprocess.run(['mountpoint','-q',str(MOUNT)]).returncode==0:
        # Never force-unmount a share while files are open.
        subprocess.run(['umount',str(MOUNT)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    fd,name=tempfile.mkstemp(dir=DIRECTORY)
    try:
        with os.fdopen(fd,'w') as f:
            for k in ('username','password','domain'):
                if data[k]:f.write(k+'='+data[k]+'\n')
        options=f'credentials={name},ip={address},uid={account.pw_uid},gid={account.pw_gid},file_mode=0600,dir_mode=0700,nosuid,nodev,noexec'
        try:result=subprocess.run(['mount','-t','cifs','//'+data['server']+'/'+data['share'],str(MOUNT),'-o',options],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=45)
        except subprocess.TimeoutExpired:raise ValueError('The SMB connection timed out. Check the NAS LAN IP address, VPN, firewall and SMB port 445.') from None
        if result.returncode:raise ValueError(mount_failure(result.stderr))
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
        print(json.dumps({'error':str(e) if isinstance(e,ValueError) else 'Network share connection failed.',**({'code':'nas_resolution_required'} if isinstance(e,ResolutionRequired) else {})}));sys.exit(1)
