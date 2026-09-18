#!/bin/bash
set -euo pipefail
# Only the dedicated, installer-owned environment is ever modified.
test -f /etc/telegram-stl-managed
exec /usr/bin/python3 - "$@" <<'PY'
import hashlib,json,os,pathlib,shutil,subprocess,sys,tarfile,time,uuid
app=pathlib.Path('/home/stl/telegram-stl');journal=pathlib.Path('/home/stl/telegram-stl-update-state.json')
session=pathlib.Path('/home/stl/.local/share/telegram-stl-tdl')
action=sys.argv[1]
def save(state):
 p=journal.with_suffix('.tmp');p.write_text(json.dumps(state));p.chmod(0o600);p.replace(journal)
def rollback(state):
 backup=pathlib.Path(state['backup']);old=backup/'application'
 if old.exists():
  for location in (app,backup/'new/telegram-stl'):
   if (location/'downloads').exists() and not (old/'downloads').exists():shutil.move(str(location/'downloads'),str(old/'downloads'))
  if app.exists():
   if (app/'data').exists():shutil.move(str(app/'data'),str(backup/'failed-data'))
   app.rename(backup/'failed-application')
  old.rename(app)
  if (backup/'private-data.tar.gz').exists():
   if (app/'data').exists():shutil.rmtree(app/'data')
   with tarfile.open(backup/'private-data.tar.gz') as archive:archive.extractall(app,filter='data')
  subprocess.run(['chown','-hR','stl:stl',str(app/'data')],check=True)
 if (backup/'telegram-session.tar.gz').exists():
  if session.exists():shutil.rmtree(session)
  with tarfile.open(backup/'telegram-session.tar.gz') as archive:archive.extractall(session.parent,filter='data')
  subprocess.run(['chown','-hR','stl:stl',str(session)],check=True)
 (app/'data/update-installing').unlink(missing_ok=True)
 state['phase']='rolled_back';save(state)
 print('Previous version and saved state restored. Downloads outside the application were not changed.',flush=True)
if action in ('rollback','finish','recover'):
 if not journal.exists():sys.exit(0)
 state=json.loads(journal.read_text())
 if action=='rollback':rollback(state)
 elif action=='finish':state['phase']='complete';save(state);(app/'data/update-installing').unlink(missing_ok=True)
 elif state['phase'] in ('switching','backed_up') or (state['phase']=='testing' and time.time()-state['time']>300):rollback(state)
 sys.exit(0)
if action!='install':raise SystemExit('Unknown update operation')
if journal.exists():
 oldstate=json.loads(journal.read_text())
 if oldstate['phase'] not in ('complete','rolled_back'):rollback(oldstate)
payload=pathlib.Path(sys.argv[2]);expected=sys.argv[3]
h=hashlib.sha256()
with payload.open('rb') as stream:
 while block:=stream.read(1024*1024):h.update(block)
if h.hexdigest()!=expected:raise SystemExit('Runtime checksum mismatch')
base=pathlib.Path('/home/stl/telegram-stl-update-backups');base.mkdir(mode=0o700,exist_ok=True)
backup=base/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]);backup.mkdir(mode=0o700)
new=backup/'new';new.mkdir()
with tarfile.open(payload) as archive:
 for member in archive:
  p=pathlib.PurePosixPath(member.name)
  if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0]!='telegram-stl':raise SystemExit('Unsafe runtime path')
  if len(p.parts)>1 and p.parts[1] in ('data','downloads','dist','.git'):raise SystemExit('Private state found in runtime package')
  if member.isdev() or member.isfifo():raise SystemExit('Unsupported runtime member')
 archive.extractall(new,filter='data')
staged=new/'telegram-stl'
subprocess.run([str(staged/'runtime/python/bin/python3'),'-I','-c','import sqlite3,ssl,PIL,pypdf'],check=True)
subprocess.run(['chown','-hR','stl:stl',str(staged)],check=True)
print('Backing up saved settings, sessions and history. External download folders are untouched.',flush=True)
with tarfile.open(backup/'private-data.tar.gz','w:gz') as archive:
 def safe(info):return None if info.name.endswith('.sock') else info
 archive.add(app/'data',arcname='data',filter=safe)
if session.exists():
 with tarfile.open(backup/'telegram-session.tar.gz','w:gz') as archive:archive.add(session,arcname=session.name,filter=safe)
state={'backup':str(backup),'phase':'backed_up','time':time.time()};save(state)
try:
 (app/'data/update-installing').touch()
 state['phase']='switching';save(state)
 app.rename(backup/'application')
 shutil.move(str(backup/'application/data'),str(staged/'data'))
 # Preserve optional downloads inside the managed installation as well.
 if (backup/'application/downloads').exists():shutil.move(str(backup/'application/downloads'),str(staged/'downloads'))
 staged.rename(app)
 state['phase']='testing';save(state)
 print('Installed version '+(app/'VERSION').read_text().strip()+'. Checking startup next.',flush=True)
except BaseException:
 rollback(state);raise
PY
