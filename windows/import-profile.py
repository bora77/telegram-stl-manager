#!/usr/bin/env python3
"""Import an optional private test profile once, never over an existing setup."""
import base64,json,os,re,sqlite3,sys
from pathlib import Path
ALLOWED={'source.json','creators.json','incoming-folders.json','subscriptions.json','settings.json','telegram-servers.json','setup-share.json'}
BLOBS={'download-history.sqlite3','telegram-catalog.sqlite3','collages.sqlite3','mmf/manager.sqlite3','mmf/session.cookies','telegram-session'}
def install_mount(profile):
    value=profile.get('mount_path')
    if not value:return
    if not re.fullmatch(r'/mnt/[A-Za-z0-9][A-Za-z0-9_-]*',value):raise ValueError('Invalid imported mount path.')
    directory=Path('/etc/telegram-stl');directory.mkdir(mode=0o700,exist_ok=True)
    path=directory/'mount-path';path.write_text(value);path.chmod(0o600)

def install(root,profile,home=None):
    target=root/'data';marker=target/'private-profile-imported'
    if marker.exists():return False
    current=json.loads((target/'creators.json').read_text())
    if current.get('creators') or (target/'subscriptions.json').exists():raise ValueError('Existing configuration was kept; a test profile can only initialize a fresh installation.')
    files=profile.get('files',{})
    if set(files)-ALLOWED:raise ValueError('Unexpected private profile file.')
    if set(profile.get('mmf_settings',{}))-{'creators','settings'}:raise ValueError('Unexpected MMF profile setting.')
    if set(profile.get('blobs',{}))-BLOBS:raise ValueError('Unexpected private binary file.')
    blobs={name:base64.b64decode(value,validate=True) for name,value in profile.get('blobs',{}).items()}
    session=(home or Path.home())/'.local/share/telegram-stl-tdl/data/telegram-stl-trial'
    if 'telegram-session' in blobs and session.exists():raise ValueError('Existing Telegram session was kept.')
    source=files.get('source.json',{})
    from source_scope import SourceScope
    scope=SourceScope(**source)
    if any(scope.topic_id(r.get('topic_url')) is None for r in files.get('creators.json',{}).get('creators',[])):raise ValueError('Profile creator is outside its configured source.')
    target.mkdir(exist_ok=True,mode=0o700)
    for name,value in files.items():
        path=target/name;tmp=path.with_suffix('.importing');tmp.write_text(json.dumps(value));tmp.chmod(0o600);tmp.replace(path)
    mmf=target/'mmf';mmf.mkdir(exist_ok=True,mode=0o700)
    if 'mmf/manager.sqlite3' not in blobs:
        db=sqlite3.connect(mmf/'manager.sqlite3')
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                for name,value in profile.get('mmf_settings',{}).items():
                    db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',(name,json.dumps(value)))
        finally:db.close()
    for name,content in blobs.items():
        path=session if name=='telegram-session' else target/name
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        tmp=path.with_suffix('.importing');tmp.write_bytes(content);tmp.chmod(0o600);tmp.replace(path)
    if 'telegram-session' in blobs:(target/'telegram-connected').touch(mode=0o600)
    marker.write_text('Private test profile imported.\n');marker.chmod(0o600)
    return True
if __name__=='__main__':
    os.umask(0o077);root=Path('/home/stl/telegram-stl');sys.path.insert(0,str(root))
    profile=json.loads(Path(sys.argv[1]).read_text())
    if len(sys.argv)>2 and sys.argv[2]=='--mount':install_mount(profile)
    else:install(root,profile)
