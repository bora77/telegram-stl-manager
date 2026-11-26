#!/usr/bin/env python3
"""Package the runtime and automatic Windows/WSL installer; exclude private state."""
import hashlib,json,tarfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
runtime=ROOT/'dist/telegram-stl-linux-x64.tar.gz'
if not runtime.is_file():raise SystemExit('Build the runtime first with tools/package-runtime.py')
# Reject stale runtime payloads: the installer must contain the current public files.
import importlib.util
spec=importlib.util.spec_from_file_location('release_files',ROOT/'tools/package-release.py');public=importlib.util.module_from_spec(spec);spec.loader.exec_module(public)
with tarfile.open(runtime,'r|gz') as archive:public.verify_runtime(ROOT,archive)

output=ROOT/'dist/Telegram-STL-Manager-Windows.zip'
with zipfile.ZipFile(output.with_suffix('.tmp'),'w',compression=zipfile.ZIP_DEFLATED) as out:
    launchers=[action+'-Windows.cmd' for action in ('Install','Start','Stop','Update','Uninstall')]
    support=('Install.ps1','Start.ps1','Update.ps1','Uninstall.ps1','bootstrap.sh','session.sh','share-helper.py','preserve-downloads.py','update.sh')
    for name in launchers+list(support):
        out.write(ROOT/'windows'/name,'Telegram-STL-Manager/'+('' if name in launchers else 'support/')+name)
    for name in ('INSTALL.md','AI-INSTALL-PROMPT.md'):
        out.write(ROOT/name,'Telegram-STL-Manager/'+name)
    out.write(ROOT/'USAGE-NOTICE.md','Telegram-STL-Manager/support/USAGE-NOTICE.md')
    out.write(runtime,'Telegram-STL-Manager/support/'+runtime.name)
    out.writestr('Telegram-STL-Manager/support/'+runtime.name+'.sha256',hashlib.sha256(runtime.read_bytes()).hexdigest()+'  '+runtime.name+'\n')
    out.write(ROOT/'windows/README.md','Telegram-STL-Manager/README.md')
output.with_suffix('.tmp').replace(output)
output.with_suffix('.zip.sha256').write_text(hashlib.sha256(output.read_bytes()).hexdigest()+'  '+output.name+'\n')
print(output)
